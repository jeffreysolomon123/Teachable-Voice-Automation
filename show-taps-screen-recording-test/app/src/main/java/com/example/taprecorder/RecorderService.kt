package com.example.taprecorder

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.Display
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Foreground service that owns the MediaProjection, VirtualDisplay and MediaRecorder.
 *
 * It must be a foreground service (type mediaProjection): Android 10+ refuses screen capture from
 * a plain background component, and the ongoing notification is what keeps the recording alive
 * while another app is in front.
 */
class RecorderService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    // Finalizing (MediaRecorder.stop + MediaStore update) can take a moment; keep it off the main thread.
    private val worker = Executors.newSingleThreadExecutor()
    private val finishing = AtomicBoolean(false)

    private var projection: MediaProjection? = null
    private var recorder: MediaRecorder? = null
    private var display: VirtualDisplay? = null
    private var output: RecordingStore.Output? = null
    private var startedAt = 0L

    // Android 14+: a callback MUST be registered on the projection before createVirtualDisplay(),
    // otherwise createVirtualDisplay throws. It also tells us when the system/user ends the capture
    // (e.g. the "Stop sharing" status-bar chip, or the projection being revoked).
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() = stopRequested()
    }

    private val countdownTick = object : Runnable {
        override fun run() {
            val left = RecorderState.state.value.countdown - 1
            if (left > 0) {
                RecorderState.update { it.copy(countdown = left) }
                handler.postDelayed(this, 1000)
            } else {
                startRecording()
            }
        }
    }

    private val timerTick = object : Runnable {
        override fun run() {
            val seconds = (SystemClock.elapsedRealtime() - startedAt) / 1000
            RecorderState.update { it.copy(elapsedSeconds = seconds) }
            handler.postDelayed(this, 500)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel(CHANNEL_ID, "Screen recording", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> begin(intent)
            ACTION_STOP -> stopRequested()
        }
        // Do not let the system restart us after being killed: the projection token is gone by then.
        return START_NOT_STICKY
    }

    /** Called right after the user granted screen capture. */
    private fun begin(intent: Intent) {
        // startForegroundService() obliges us to call startForeground() within seconds, so do it first.
        // On Android 14+ this must happen BEFORE getMediaProjection(), and the type must be mediaProjection.
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification("Starting…"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } catch (e: Exception) {
            fail("Could not start recording service: ${e.message}")
            return
        }
        if (RecorderState.state.value.phase in setOf(Phase.Countdown, Phase.Recording, Phase.Saving)) return

        finishing.set(false)
        try {
            val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
            val data = IntentCompat.getParcelableExtra(intent, EXTRA_RESULT_DATA, Intent::class.java)
                ?: error("Missing projection result")
            val manager = getSystemService(MediaProjectionManager::class.java)
            val p = manager.getMediaProjection(resultCode, data) ?: error("No MediaProjection returned")
            p.registerCallback(projectionCallback, handler)
            projection = p
        } catch (e: Exception) {
            fail("Could not get screen capture: ${e.message}")
            return
        }

        RecorderState.update { RecorderUiState(phase = Phase.Countdown, countdown = COUNTDOWN_SECONDS) }
        handler.postDelayed(countdownTick, 1000)
    }

    private fun startRecording() {
        try {
            val (width, height) = screenSize()
            val out = RecordingStore.create(this).also { output = it }

            val r = (if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder())
            recorder = r
            // No setAudioSource(): no microphone permission is needed.
            r.setVideoSource(MediaRecorder.VideoSource.SURFACE)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
            r.setVideoSize(width, height)
            r.setVideoFrameRate(30)
            r.setVideoEncodingBitRate(8_000_000)
            r.setOutputFile(out.pfd.fileDescriptor)
            r.prepare()

            // Callback was registered in begin(). Android 14+ allows only one VirtualDisplay per projection.
            display = projection!!.createVirtualDisplay(
                "TapScreenRecorder", width, height, resources.displayMetrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, r.surface, null, handler,
            )
            r.start()
            // Anchor the tap log to the video: event times are converted to ms since this instant.
            val startUptime = SystemClock.uptimeMillis()
            TapLog.begin(startUptime, sessionMeta(out.name, width, height, startUptime))

            startedAt = SystemClock.elapsedRealtime()
            RecorderState.update { it.copy(phase = Phase.Recording, elapsedSeconds = 0) }
            getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification("Recording…"))
            handler.post(timerTick)
        } catch (e: Exception) {
            fail("Could not start recording: ${e.message}")
        }
    }

    /** Stop button (notification or app) or projection revoked by the system. */
    private fun stopRequested() {
        when (RecorderState.state.value.phase) {
            Phase.Countdown -> fail(null) // cancelled before anything was recorded
            Phase.Recording -> worker.execute { finishRecording() }
            Phase.Saving -> Unit
            else -> if (projection == null) stopSelf() // stale notification action; nothing to stop
        }
    }

    /** Stops the recorder safely, publishes the file to the Gallery and shuts the service down. */
    private fun finishRecording() {
        if (!finishing.compareAndSet(false, true)) return
        handler.removeCallbacks(timerTick)
        RecorderState.update { it.copy(phase = Phase.Saving) }

        val log = TapLog.end(SystemClock.uptimeMillis())
        var valid = true
        try {
            recorder?.stop()
        } catch (e: RuntimeException) {
            // Thrown when stop() is called before any frame was written (stopped too quickly);
            // the output file is invalid in that case.
            valid = false
        }
        releaseResources()

        val out = output
        output = null
        if (out != null && valid) {
            try {
                RecordingStore.publish(this, out.uri)
                RecorderState.update { it.copy(phase = Phase.Saved, savedUri = out.uri, message = null) }
                if (log != null) saveLog(out.name, log)
            } catch (e: Exception) {
                RecordingStore.discard(this, out.uri)
                RecorderState.update { it.copy(phase = Phase.Idle, message = "Could not save video: ${e.message}") }
            }
        } else {
            out?.let { RecordingStore.discard(this, it.uri) }
            RecorderState.update { it.copy(phase = Phase.Idle, message = "Recording was too short to save") }
        }
        shutdown()
    }

    /** Abort without a video (cancelled countdown or a setup failure). [message] is shown in the UI. */
    private fun fail(message: String?) {
        TapLog.discard()
        handler.removeCallbacks(countdownTick)
        handler.removeCallbacks(timerTick)
        releaseResources()
        output?.let { RecordingStore.discard(this, it.uri) }
        output = null
        RecorderState.update { RecorderUiState(phase = Phase.Idle, message = message) }
        shutdown()
    }

    private fun releaseResources() {
        try { recorder?.release() } catch (_: Exception) {}
        recorder = null
        try { display?.release() } catch (_: Exception) {}
        display = null
        projection?.let {
            // Unregister first so our own stop() does not re-enter onStop().
            it.unregisterCallback(projectionCallback)
            try { it.stop() } catch (_: Exception) {}
        }
        projection = null
        try { output?.pfd?.close() } catch (_: Exception) {}
    }

    private fun shutdown() {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        // Best effort if the system tears the service down while recording: still save what we have.
        handler.removeCallbacks(countdownTick)
        when (RecorderState.state.value.phase) {
            Phase.Recording -> finishRecording()
            Phase.Countdown -> fail(null)
            else -> Unit
        }
        worker.shutdown()
        super.onDestroy()
    }

    /** Writes the tap log next to the video. A failure here never affects the saved video. */
    private fun saveLog(videoName: String, log: JSONObject) {
        try {
            val eventCount = log.getJSONArray("events").length()
            val (uri, name) = RecordingStore.saveLog(this, videoName, log.toString(2))
            RecorderState.update { it.copy(logUri = uri, logName = name, logEventCount = eventCount) }
        } catch (e: Exception) {
            RecorderState.update { it.copy(message = "Video saved, but the tap log could not be saved: ${e.message}") }
        }
    }

    /** Header of the tap-log JSON: what was recorded, on which device, and the clock anchors. */
    private fun sessionMeta(videoName: String, width: Int, height: Int, startUptime: Long): JSONObject {
        val rotation = getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)?.rotation
        return JSONObject()
            .put("schemaVersion", TapLog.SCHEMA_VERSION)
            .put("video", JSONObject().put("fileName", videoName).put("width", width).put("height", height))
            .put(
                "device", JSONObject()
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL)
                    .put("sdkInt", Build.VERSION.SDK_INT)
                    .put("densityDpi", resources.displayMetrics.densityDpi)
                    .put("rotation", rotation),
            )
            .put(
                "clock", JSONObject()
                    .put("recordingStartUptimeMs", startUptime)
                    .put("recordingStartWallClockMs", System.currentTimeMillis())
                    .put(
                        "note",
                        "videoTimeMs = eventUptimeMs - recordingStartUptimeMs. Anchored right after " +
                            "MediaRecorder.start(); the first video frame can lag this by a few tens of ms. " +
                            "Bounds are screen pixels, which equal video pixels (video is recorded at native size).",
                    ),
            )
            .put(
                "logger", JSONObject()
                    .put("enabledAtStart", TapLoggerService.isEnabled(this))
                    .put("connectedAtStart", TapLog.serviceConnected),
            )
    }

    /** Native resolution of the display, rounded down to even numbers (H.264 requires that). */
    private fun screenSize(): Pair<Int, Int> {
        val wm = getSystemService(WindowManager::class.java)
        val (w, h) = if (Build.VERSION.SDK_INT >= 30) {
            wm.maximumWindowMetrics.bounds.let { it.width() to it.height() }
        } else {
            val metrics = DisplayMetrics()
            @Suppress("DEPRECATION") wm.defaultDisplay.getRealMetrics(metrics)
            metrics.widthPixels to metrics.heightPixels
        }
        return (w and 1.inv()) to (h and 1.inv())
    }

    private fun buildNotification(text: String): Notification {
        val stop = PendingIntent.getService(
            this, 0, Intent(this, RecorderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val open = PendingIntent.getActivity(
            this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_record)
            .setContentTitle("TapScreenRecorder")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val ACTION_START = "com.example.taprecorder.START"
        private const val ACTION_STOP = "com.example.taprecorder.STOP"
        private const val EXTRA_RESULT_CODE = "result_code"
        private const val EXTRA_RESULT_DATA = "result_data"
        private const val CHANNEL_ID = "recording"
        private const val NOTIFICATION_ID = 1
        private const val COUNTDOWN_SECONDS = 3

        /** Pass the screen-capture permission result straight to the service. */
        fun start(context: Context, resultCode: Int, data: Intent) {
            val intent = Intent(context, RecorderService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_RESULT_CODE, resultCode)
                .putExtra(EXTRA_RESULT_DATA, data)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, RecorderService::class.java).setAction(ACTION_STOP))
        }
    }
}
