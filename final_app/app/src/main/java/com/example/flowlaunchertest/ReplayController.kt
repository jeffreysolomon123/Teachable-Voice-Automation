package com.example.flowlaunchertest

import android.content.Context
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.Locale

/**
 * The replay loop: backend action -> execute -> wait -> screenshot -> /replay/step -> next action.
 * All decisions (which element, whether it worked, when to ask the user) come from the backend.
 * Progress is shown on the overlay, spoken with the system TTS (the assistant's WebView is in the
 * background while the target app is on screen), and sent to [eventSink] for the chat UI.
 */
object ReplayController {
    private const val TAG = "ReplayController"
    private const val MAX_TRANSIENT_ERRORS = 3

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var job: Job? = null
    private var sessionId: String? = null
    private var client: BackendClient? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    /** Completed by the overlay's Confirm/Stop buttons while an ASK_USER is shown. */
    @Volatile
    private var pendingAnswer: CompletableDeferred<Boolean>? = null

    /** (type, text) with type = status | ask | completed | failed | stopped. Set by MainActivity. */
    @Volatile
    var eventSink: ((String, String) -> Unit)? = null

    val isRunning: Boolean get() = job?.isActive == true

    /** Why a replay can't start on this device right now, or null when it can. */
    fun preflight(context: Context): String? {
        val missing = buildList {
            if (!Settings.canDrawOverlays(context)) add("overlay permission")
            if (!WindowWatcherAccessibilityService.isEnabledInSettings(context)) add("accessibility service")
        }
        if (missing.isNotEmpty()) return "Missing ${missing.joinToString(" and ")}. Open Device setup to grant it."
        if (!WindowWatcherAccessibilityService.isConnected) {
            return "The accessibility service is enabled but not running. Turn it off and on in Settings."
        }
        if (isRunning) return "A task is already running."
        return null
    }

    fun start(context: Context, backendUrl: String, slotPayload: JSONObject) {
        if (isRunning) return
        initTts(context.applicationContext)
        OverlayService.start(context)
        job = scope.launch {
            try {
                run(BackendClient(backendUrl).also { client = it }, slotPayload)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Replay failed", e)
                finish("failed", "The task stopped because of an error: ${e.message?.take(120)}")
            } finally {
                pendingAnswer = null
            }
        }
    }

    fun answer(confirmed: Boolean) {
        pendingAnswer?.complete(confirmed)
    }

    fun stop() {
        val sid = sessionId
        val c = client
        val wasRunning = isRunning
        job?.cancel()
        pendingAnswer = null
        if (sid != null && c != null) {
            scope.launch { runCatching { withContext(Dispatchers.IO) { c.stop(sid) } } }
        }
        OverlayBus.state.value = OverlayState.Hidden
        if (wasRunning) emit("stopped", "Task stopped.")
    }

    private suspend fun run(api: BackendClient, payload: JSONObject) {
        val service = WindowWatcherAccessibilityService.instance
            ?: return finish("failed", "The accessibility service isn't running.")
        val executor = ActionExecutor(service)
        val slots = payload.optJSONObject("slots") ?: JSONObject()

        status("Finding the right workflow…")
        val match = io { api.matchFlow(payload) }
        if (!match.optBoolean("found")) {
            return finish("failed", "I don't have an executable workflow for that yet. Teach it first, then try again.")
        }
        val missing = match.optJSONArray("missing_slots")
        if (missing != null && missing.length() > 0) {
            return finish("failed", "I still need: ${(0 until missing.length()).joinToString { missing.getString(it) }}.")
        }
        var response = io { api.start(match.getString("flow_id"), slots, null) }
        val sid = response.getString("session_id").also { sessionId = it }
        // Display size in pixels = size of the full-resolution screenshot (gesture coordinate space).
        var displayW = 0
        var displayH = 0
        var transientErrors = 0
        var skipExecute = false

        while (true) {
            val status = response.optString("status")
            val action = response.optJSONObject("action")
            val stepLabel = "Step ${response.optInt("step")} of ${response.optInt("total_steps")}"
            if (status in setOf("completed", "failed", "stopped") || action == null || action.optString("action") == "STOP") {
                val message = response.optString("message").ifBlank { null }
                return when (status) {
                    "completed" -> finish("completed", message?.takeIf { it != "Flow completed." } ?: "Done! The task is complete.")
                    "stopped" -> finish("stopped", message ?: "Task stopped.")
                    else -> finish("failed", message ?: "The task couldn't be completed.")
                }
            }

            var result = ActionExecutor.OK
            if (action.getString("action") == "ASK_USER") {
                val reason = action.optString("reason")
                val answer = CompletableDeferred<Boolean>().also { pendingAnswer = it }
                OverlayBus.state.value = OverlayState.Asking(reason)
                emit("ask", reason)
                speak(reason)
                val confirmed = answer.await()
                pendingAnswer = null
                status(if (confirmed) "Continuing…" else "Stopping…")
                // The backend answers with a WAIT (re-capture) or STOP action.
                response = io { api.confirm(sid, confirmed, action.optString("confirmation_id").ifBlank { null }) }
                continue
            }

            if (!skipExecute) {
                status("$stepLabel: ${describe(action)}")
                OverlayBus.hideForInput.value = true // the overlay must not intercept injected touches
                try {
                    result = executor.execute(action, displayW, displayH)
                } finally {
                    OverlayBus.hideForInput.value = false
                }
                delay(action.optLong("settle_ms", 1000))
            }
            skipExecute = false

            // Capture with the overlay hidden so it isn't part of the screenshot.
            OverlayBus.hideForInput.value = true
            delay(150)
            val bitmap = try { service.captureScreenshot() } finally { OverlayBus.hideForInput.value = false }
                ?: return finish("failed", "I couldn't take a screenshot.")
            displayW = bitmap.width
            displayH = bitmap.height
            val upload = withContext(Dispatchers.Default) { BackendClient.prepare(bitmap) }
            bitmap.recycle()

            status("$stepLabel: looking at the screen…")
            try {
                response = io { api.step(sid, upload, result) }
                transientErrors = 0
            } catch (e: BackendClient.BackendException) {
                if (!e.retryable || ++transientErrors > MAX_TRANSIENT_ERRORS) throw e
                Log.w(TAG, "Transient backend error ${e.code}: ${e.message}; re-capturing")
                delay(1500)
                skipExecute = true // don't repeat the action, just send a fresh screenshot
            }
        }
    }

    private fun status(text: String) {
        OverlayBus.state.value = OverlayState.Status(text)
        emit("status", text)
    }

    private fun finish(type: String, text: String) {
        Log.i(TAG, "Replay $type: $text")
        OverlayBus.state.value = if (type == "completed") OverlayState.Ready else OverlayState.Blocked(text)
        emit(type, text)
        speak(text)
    }

    private fun emit(type: String, text: String) {
        runCatching { eventSink?.invoke(type, text) }
    }

    private fun initTts(context: Context) {
        if (tts != null) return
        tts = TextToSpeech(context) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) tts?.language = Locale("en", "IN")
        }
    }

    private fun speak(text: String) {
        if (ttsReady) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "replay")
    }

    private fun describe(action: JSONObject): String = when (action.getString("action")) {
        "TAP" -> "tapping ${action.optString("reason").substringAfter("(").substringBefore(")").take(30)}"
        "TYPE" -> "typing"
        "SWIPE" -> "scrolling"
        "LAUNCH_APP" -> "opening ${action.optString("app")}"
        "WAIT" -> "checking the screen"
        "BACK" -> "going back"
        else -> action.getString("action").lowercase()
    }

    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }
}
