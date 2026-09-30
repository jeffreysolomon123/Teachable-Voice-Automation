package com.example.flowlaunchertest

import android.graphics.Bitmap
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * HTTP client for the FastAPI backend. The app talks ONLY to this backend: no Groq, OCR,
 * segmentation or vision calls happen on the phone, and no API keys are stored here.
 * All calls block: run them on Dispatchers.IO.
 */
class BackendClient(baseUrl: String) {

    class BackendException(val code: String, message: String, val retryable: Boolean) : IOException(message)

    /** A screenshot prepared for upload plus the size the backend's coordinates will refer to. */
    data class Upload(val jpeg: ByteArray, val width: Int, val height: Int)

    companion object {
        private val JSON = "application/json".toMediaType()
        private val JPEG = "image/jpeg".toMediaType()
        private const val MAX_UPLOAD_WIDTH = 1080
        private const val JPEG_QUALITY = 85

        private val http = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS) // segmentation + LLM tiers can take a while
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()

        // TEACH uploads a screen recording (tens of MB over Wi-Fi).
        private val uploadHttp = http.newBuilder()
            .writeTimeout(10, TimeUnit.MINUTES)
            .readTimeout(2, TimeUnit.MINUTES)
            .build()
        private val MP4 = "video/mp4".toMediaType()

        fun prepare(src: Bitmap): Upload {
            val scaled = if (src.width > MAX_UPLOAD_WIDTH) {
                val h = (src.height * MAX_UPLOAD_WIDTH / src.width.toFloat()).roundToInt()
                Bitmap.createScaledBitmap(src, MAX_UPLOAD_WIDTH, h, true)
            } else src
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            val upload = Upload(out.toByteArray(), scaled.width, scaled.height)
            if (scaled !== src) scaled.recycle()
            return upload
        }
    }

    private val base = baseUrl.trimEnd('/')

    fun matchFlow(slotPayload: JSONObject): JSONObject = postJson("/flows/match", slotPayload)

    fun start(flowId: String, slots: JSONObject, sessionId: String?): JSONObject =
        postJson("/replay/start", JSONObject().put("flow_id", flowId).put("slots", slots).apply {
            if (!sessionId.isNullOrBlank()) put("session_id", sessionId)
        })

    fun step(sessionId: String, upload: Upload, lastActionResult: String): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("session_id", sessionId)
            .addFormDataPart("last_action_result", lastActionResult)
            .addFormDataPart("screenshot", "screen.jpg", upload.jpeg.toRequestBody(JPEG))
            .build()
        return execute("/replay/step", body)
    }

    fun confirm(sessionId: String, confirmed: Boolean, confirmationId: String?): JSONObject =
        postJson("/replay/confirm", JSONObject().put("session_id", sessionId).put("confirmed", confirmed).apply {
            if (confirmationId != null) put("confirmation_id", confirmationId)
        })

    fun stop(sessionId: String): JSONObject = postJson("/replay/stop", JSONObject().put("session_id", sessionId))

    /** TEACH: recording + tap log + the confirmed plan -> {job_id, ...} (the server learns in the background). */
    fun uploadRecording(video: File, tapLog: File, plan: JSONObject): JSONObject {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("plan", plan.toString())
            .addFormDataPart("tap_log", tapLog.name, tapLog.asRequestBody(JSON))
            .addFormDataPart("video", video.name, video.asRequestBody(MP4))
            .build()
        return execute("/teach/recording", body, uploadHttp)
    }

    /** TEACH job status; includes "result" (flow + grounded_flow) once status == "succeeded". */
    fun teachJob(jobId: String): JSONObject = get("/teach/jobs/$jobId")

    /** Push a flow stored on the phone to the server (replaces the server's copy). */
    fun saveFlow(flow: JSONObject): JSONObject = postJson("/flows?overwrite=true", flow)

    private fun get(path: String): JSONObject = run(Request.Builder().url(base + path).get().build(), http)

    private fun postJson(path: String, body: JSONObject): JSONObject =
        execute(path, body.toString().toRequestBody(JSON))

    private fun execute(path: String, body: RequestBody, client: OkHttpClient = http): JSONObject =
        run(Request.Builder().url(base + path).post(body).build(), client)

    private fun run(request: Request, client: OkHttpClient): JSONObject {
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrNull()
            if (!response.isSuccessful) {
                val err = json?.optJSONObject("error") ?: json?.optJSONObject("detail")
                throw BackendException(
                    err?.optString("code") ?: "HTTP_${response.code}",
                    err?.optString("message") ?: "HTTP ${response.code}",
                    err?.optBoolean("retryable") ?: false,
                )
            }
            return json ?: throw BackendException("BAD_RESPONSE", "backend returned non-JSON", true)
        }
    }
}
