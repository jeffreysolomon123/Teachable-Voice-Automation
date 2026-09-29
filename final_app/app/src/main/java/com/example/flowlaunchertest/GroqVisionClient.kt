package com.example.flowlaunchertest

import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/** Kotlin port of the working Node.js Groq reference script, repurposed as a readiness check. */
class GroqVisionClient(private val apiKey: String) {

    sealed class Result {
        data object Ready : Result()
        data class Blocked(val blocker: String) : Result()
        data class Failed(val error: String) : Result()
    }

    data class CompressedImage(val base64: String, val sentWidth: Int, val sentHeight: Int)

    companion object {
        private const val TAG = "GroqVisionClient"
        private const val ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

        // Same model as the reference script. Double-check this name against the models available
        // on your current Groq account (GET https://api.groq.com/openai/v1/models) — model IDs change.
        private const val MODEL = "openai/gpt-oss-120b"

        private const val SENT_IMAGE_WIDTH = 700
        private const val JPEG_QUALITY = 70

        private val http = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()
    }

    /** Equivalent of sharp().resize({ width: 700 }).jpeg({ quality: 70 }). */
    fun compressImage(src: Bitmap): CompressedImage {
        val sentHeight = (src.height * SENT_IMAGE_WIDTH / src.width.toFloat()).roundToInt()
        val scaled = Bitmap.createScaledBitmap(src, SENT_IMAGE_WIDTH, sentHeight, true)
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (scaled !== src) scaled.recycle()
        return CompressedImage(Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP), SENT_IMAGE_WIDTH, sentHeight)
    }

    /** Same cleaning as the reference: strip a <think> block and code fences, then parse. */
    fun parseModelResponse(raw: String): JSONObject {
        val withoutThinking = raw.replaceFirst(Regex("<think>[\\s\\S]*?</think>", RegexOption.IGNORE_CASE), "").trim()
        return JSONObject(
            withoutThinking
                .replaceFirst(Regex("^```json", RegexOption.IGNORE_CASE), "")
                .replaceFirst(Regex("^```"), "")
                .replaceFirst(Regex("```$"), "")
                .trim()
        )
    }

    private fun buildPrompt(width: Int, height: Int) =
        "You are looking at a screenshot of a mobile app, exactly ${width}x${height} pixels. " +
            "Determine if this is the app's normal, usable home or main screen, or if something is blocking it " +
            "(a splash screen, a permission dialog, a login/update prompt, a loading spinner). " +
            "Respond with ONLY valid JSON, no markdown fences: {\"ready\": true} if it's the normal usable screen, " +
            "or {\"ready\": false, \"blocker\": \"<short phrase describing what's blocking, e.g. 'notification permission dialog'>\"} " +
            "if something is in the way."

    /** Blocking network call — run off the main thread. Never throws. */
    fun checkReadiness(screenshot: Bitmap): Result {
        if (apiKey.isBlank()) {
            Log.e(TAG, "Groq API key missing")
            return Result.Failed("API key missing")
        }
        return try {
            val image = compressImage(screenshot)
            Log.i(TAG, "Sending ${image.sentWidth}x${image.sentHeight} JPEG (${image.base64.length} base64 chars) to $MODEL")

            val content = JSONArray()
                .put(JSONObject().put("type", "text").put("text", buildPrompt(image.sentWidth, image.sentHeight)))
                .put(
                    JSONObject().put("type", "image_url")
                        .put("image_url", JSONObject().put("url", "data:image/jpeg;base64,${image.base64}"))
                )
            val body = JSONObject()
                .put("model", MODEL)
                .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", content)))
                .put("max_tokens", 150)
                .put("temperature", 0)
                .put("reasoning_effort", "none")

            val request = Request.Builder()
                .url(ENDPOINT)
                .header("Authorization", "Bearer $apiKey")
                .post(body.toString().toRequestBody("application/json".toMediaType()))
                .build()

            val startedAt = System.currentTimeMillis()
            http.newCall(request).execute().use { response ->
                val responseBody = response.body?.string().orEmpty()
                Log.i(TAG, "HTTP ${response.code} in ${System.currentTimeMillis() - startedAt}ms")
                if (!response.isSuccessful) {
                    Log.e(TAG, "Groq error body: $responseBody")
                    val apiMessage = runCatching {
                        JSONObject(responseBody).getJSONObject("error").getString("message")
                    }.getOrNull()
                    return Result.Failed(shorten("HTTP ${response.code}" + (apiMessage?.let { ": $it" } ?: "")))
                }

                val raw = JSONObject(responseBody)
                    .getJSONArray("choices").getJSONObject(0)
                    .getJSONObject("message").getString("content")
                Log.i(TAG, "Raw model response: $raw")

                val parsed = parseModelResponse(raw)
                if (parsed.getBoolean("ready")) {
                    Result.Ready
                } else {
                    Result.Blocked(parsed.optString("blocker").ifBlank { "unspecified blocker" })
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Readiness check failed", e)
            Result.Failed(shorten("${e.javaClass.simpleName}: ${e.message}"))
        }
    }

    private fun shorten(s: String) = if (s.length <= 80) s else s.take(77) + "..."
}
