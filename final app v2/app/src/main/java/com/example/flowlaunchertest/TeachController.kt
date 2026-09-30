package com.example.flowlaunchertest

import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * TEACH end to end on the phone:
 *
 *   assistant plan (TEACH) -> preflight -> screen-capture consent -> [TeachRecorderService]
 *   countdown -> recording + tap log, target app opened -> user demonstrates -> Stop (overlay)
 *   -> upload video + tap log + plan to POST /teach/recording -> poll GET /teach/jobs/{id}
 *   -> server returns the learned flow + grounded_flow.json -> saved in [LocalFlowStore]
 *
 * Progress goes to the overlay and to [eventSink] (the assistant chat): (type, text) with type =
 * status | recording | stopped | learned | failed.
 */
object TeachController {
    private const val TAG = "TeachController"
    private const val POLL_MS = 3000L
    private const val MAX_WAIT_MS = 15 * 60 * 1000L

    /**
     * Demo build: nothing is actually recorded or sent to the server. TEACH looks the same
     * (countdown, target app opened, red REC pill with Stop), then the assistant UI shows a
     * scripted "processing" bar and a "Recording stored" banner.
     */
    const val DEMO_MODE = true
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var simStartedAt = 0L
    private val simTick = object : Runnable {
        override fun run() {
            OverlayBus.state.value = OverlayState.Recording((SystemClock.elapsedRealtime() - simStartedAt) / 1000)
            mainHandler.postDelayed(this, 1000)
        }
    }

    enum class Phase { Idle, AwaitingConsent, Countdown, Recording, Learning }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var learnJob: Job? = null

    @Volatile var phase = Phase.Idle
        private set
    @Volatile var eventSink: ((String, String) -> Unit)? = null

    private var plan: JSONObject? = null
    private var targetPackage: String? = null
    private var weTurnedOnShowTaps = false
    private var showTapsUnknown = false

    val isBusy: Boolean get() = phase != Phase.Idle

    /** Why TEACH can't start now (null = OK). Also switches "Show taps" on when permitted. */
    fun preflight(context: Context): String? {
        if (DEMO_MODE) {
            if (!Settings.canDrawOverlays(context)) return "Missing overlay permission. Open Device setup to grant it."
            if (isBusy) return "I'm already recording a demonstration."
            return null
        }
        val missing = buildList {
            if (!Settings.canDrawOverlays(context)) add("overlay permission")
            if (!WindowWatcherAccessibilityService.isEnabledInSettings(context)) add("accessibility service")
        }
        if (missing.isNotEmpty()) return "Missing ${missing.joinToString(" and ")}. Open Device setup to grant it."
        if (!WindowWatcherAccessibilityService.isConnected) {
            return "The accessibility service is enabled but not running. Turn it off and on in Settings."
        }
        if (isBusy) return "I'm already working on a demonstration."
        if (ReplayController.isRunning) return "A task is running. Stop it first."
        showTapsUnknown = ShowTaps.isOn(context) == null && !ShowTaps.canChange(context)
        if (ShowTaps.isOn(context) != true) {
            weTurnedOnShowTaps = ShowTaps.canChange(context) && ShowTaps.set(context, true)
            if (!weTurnedOnShowTaps && ShowTaps.isOn(context) == false) {
                ShowTaps.openDeveloperOptions(context)
                return "Please turn on \"Show taps\" in Developer options (I just opened it), then ask me to teach again. " +
                    "I need it to see where you tap in the recording."
            }
        }
        return null
    }

    /** Remembers the plan; the caller then asks for screen-capture consent. */
    fun prepare(context: Context, planJson: JSONObject): String? {
        preflight(context)?.let { return it }
        val app = planJson.optString("app_name").ifBlank { planJson.optJSONObject("slots")?.optString("app").orEmpty() }
        val pkg = AppResolver.packageFor(context, app, planJson.optString("package").ifBlank { null })
            ?: return "I couldn't find the app \"$app\" on this phone."
        plan = JSONObject(planJson.toString()).put("app_name", app).put("package", pkg)
        targetPackage = pkg
        phase = Phase.AwaitingConsent
        return null
    }

    /** Result of the screen-capture consent dialog (resultCode RESULT_OK = allowed). */
    fun onConsent(context: Context, granted: Boolean, resultCode: Int, data: Intent?) {
        if (phase != Phase.AwaitingConsent) return
        if (!granted || data == null) {
            reset(context)
            return emit("failed", "Screen recording wasn't allowed, so I can't learn this one.")
        }
        OverlayService.start(context)
        TeachRecorderService.start(context, resultCode, data)
    }

    /** Demo: simulated recording (no MediaProjection, no video, no tap log). Call after [prepare]. */
    fun startSimulated(context: Context) {
        val app = context.applicationContext
        OverlayService.start(app)
        onCountdown()
        var left = 3
        OverlayBus.state.value = OverlayState.Status("Recording starts in $left…")
        mainHandler.postDelayed(object : Runnable {
            override fun run() {
                left -= 1
                if (phase != Phase.Countdown) return // stopped during the countdown
                if (left > 0) {
                    OverlayBus.state.value = OverlayState.Status("Recording starts in $left…")
                    mainHandler.postDelayed(this, 1000)
                } else {
                    simStartedAt = SystemClock.elapsedRealtime()
                    onRecordingStarted(app)
                    mainHandler.post(simTick)
                }
            }
        }, 1000)
    }

    private fun finishSimulated(context: Context) {
        val app = context.applicationContext
        val wasRecording = phase == Phase.Recording
        mainHandler.removeCallbacksAndMessages(null)
        OverlayBus.state.value = OverlayState.Hidden
        reset(app)
        bringAssistantToFront(app)
        if (wasRecording) emit("demo_recorded", "Recording captured.") else emit("stopped", "Recording cancelled.")
    }

    fun onCountdown() {
        phase = Phase.Countdown
        emit("status", "Get ready: recording starts in 3 seconds. I'll open ${plan?.optString("app_name")} for you." +
            if (showTapsUnknown) " (Make sure \"Show taps\" is on in Developer options.)" else "")
    }

    /** Recording is live: open the app being taught (the accessibility service may start activities). */
    fun onRecordingStarted(context: Context) {
        phase = Phase.Recording
        val pkg = targetPackage
        val launcher: Context = WindowWatcherAccessibilityService.instance ?: context
        val intent = pkg?.let { context.packageManager.getLaunchIntentForPackage(it) }
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        if (intent != null) runCatching { launcher.startActivity(intent) }.onFailure { Log.e(TAG, "launch $pkg failed", it) }
        emit("recording", "Recording. Do the task in ${plan?.optString("app_name")}, then tap ■ Stop on the red pill.")
    }

    /** Stop tapped on the overlay pill: logged as the recorder's own Stop tap. */
    fun stopFromOverlay(context: Context, bounds: Rect?) {
        if (DEMO_MODE) return finishSimulated(context)
        TeachRecorderService.stop(context, SystemClock.uptimeMillis(), bounds)
    }

    /** Stop from the assistant chat ("Finish" button). */
    fun stop(context: Context) {
        if (DEMO_MODE) {
            if (phase == Phase.Recording || phase == Phase.Countdown) finishSimulated(context)
            return
        }
        when (phase) {
            Phase.Recording, Phase.Countdown -> TeachRecorderService.stop(context)
            Phase.AwaitingConsent -> reset(context)
            else -> Unit
        }
    }

    fun onRecordingFailed(context: Context, message: String?) {
        reset(context)
        OverlayBus.state.value = if (message != null) OverlayState.Blocked(message) else OverlayState.Hidden
        emit(if (message != null) "failed" else "stopped", message ?: "Recording cancelled.")
    }

    /** Called on the recorder's worker thread with the finished files. */
    fun onRecordingFinished(context: Context, video: File, tapLog: File) {
        val app = context.applicationContext
        restoreShowTaps(app)
        val planNow = plan ?: JSONObject()
        if (DEMO_MODE) {
            scope.launch {
                OverlayBus.state.value = OverlayState.Hidden
                reset(app)
                bringAssistantToFront(app)
                emit("demo_recorded", "Recording captured (${video.length() / (1024 * 1024)} MB).")
            }
            return
        }
        scope.launch {
            phase = Phase.Learning
            bringAssistantToFront(app)
            emit("stopped", "Got it: ${video.length() / (1024 * 1024)} MB recorded. Sending it to the server to learn the steps…")
            learnJob = launch { learn(app, video, tapLog, planNow) }
        }
    }

    private suspend fun learn(context: Context, video: File, tapLog: File, planNow: JSONObject) {
        val api = BackendClient(Prefs.backendUrl(context))
        try {
            status("Uploading the recording…")
            val job = withContext(Dispatchers.IO) { api.uploadRecording(video, tapLog, planNow) }
            val jobId = job.getString("job_id")
            val started = SystemClock.elapsedRealtime()
            var lastStage = ""
            while (true) {
                delay(POLL_MS)
                val j = try {
                    withContext(Dispatchers.IO) { api.teachJob(jobId) }
                } catch (e: java.io.IOException) {
                    Log.w(TAG, "poll failed: ${e.message}")
                    if (SystemClock.elapsedRealtime() - started > MAX_WAIT_MS) throw e
                    continue
                }
                when (j.optString("status")) {
                    "succeeded" -> return saveLearned(context, j.getJSONObject("result"), planNow, video, tapLog)
                    "failed" -> return fail(context, "I couldn't learn from that recording: ${j.optString("error").take(200)}")
                }
                val stage = j.optString("stage")
                if (stage != lastStage && stage.isNotBlank()) {
                    lastStage = stage
                    status(STAGE_TEXT[stage] ?: "Learning…")
                }
                if (SystemClock.elapsedRealtime() - started > MAX_WAIT_MS) {
                    return fail(context, "Learning is taking too long. Check the server window on the PC.")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "learning failed", e)
            fail(context, "I couldn't reach the server to learn this (${e.message?.take(120)}). " +
                "Check that the PC server is running and the server address in settings.")
        }
    }

    private fun saveLearned(context: Context, result: JSONObject, planNow: JSONObject, video: File, tapLog: File) {
        val flow = result.getJSONObject("flow")
        val flowId = flow.getString("flow_id")
        LocalFlowStore.save(context, flowId, JSONObject()
            .put("flow", flow)
            .put("grounded_flow", result.optJSONArray("grounded_flow"))
            .put("summary", result.optJSONObject("summary"))
            .put("plan", planNow)
            .put("learned_at", System.currentTimeMillis()))
        video.delete()
        tapLog.delete()
        val steps = flow.optJSONArray("steps")?.length() ?: 0
        val slots = flow.optJSONArray("required_slots")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
        OverlayBus.state.value = OverlayState.Ready
        phase = Phase.Idle
        plan = null
        emit("learned", "Learned \"${flow.optString("description")}\" on ${flow.optString("app")}: $steps steps" +
            (if (slots.isNotEmpty()) ", and I can change the ${slots.joinToString(" and ")}" else "") +
            ". It's saved on this phone. Just ask me to do it whenever you like.")
    }

    private fun fail(context: Context, message: String) {
        OverlayBus.state.value = OverlayState.Blocked(message.take(120))
        reset(context)
        emit("failed", message)
    }

    private fun reset(context: Context) {
        restoreShowTaps(context)
        phase = Phase.Idle
        plan = null
        targetPackage = null
    }

    private fun restoreShowTaps(context: Context) {
        if (weTurnedOnShowTaps) {
            ShowTaps.set(context, false)
            weTurnedOnShowTaps = false
        }
    }

    private fun bringAssistantToFront(context: Context) {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        val launcher: Context = WindowWatcherAccessibilityService.instance ?: context
        runCatching { launcher.startActivity(intent) }.onFailure { Log.w(TAG, "bring to front failed", it) }
    }

    private fun status(text: String) {
        OverlayBus.state.value = OverlayState.Status(text)
        emit("status", text)
    }

    private fun emit(type: String, text: String) {
        Log.i(TAG, "$type: $text")
        runCatching { eventSink?.invoke(type, text) }
    }

    private val STAGE_TEXT = mapOf(
        "stage1_tap_extraction" to "Finding each of your taps in the video…",
        "stage2_flow_builder" to "Putting the steps in order…",
        "stage3_segmentation" to "Reading every screen you visited…",
        "stage4_grounding" to "Matching your taps to buttons…",
        "building_flow" to "Writing the workflow…",
    )
}
