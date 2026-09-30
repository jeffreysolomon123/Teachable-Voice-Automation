package com.example.flowlaunchertest

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.Log
import android.view.Display
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * TEACH screen recorder (ported from TapScreenRecorder's RecorderService): owns the MediaProjection,
 * VirtualDisplay and MediaRecorder, anchors [TapLog] to the video clock, and after a short
 * countdown opens the app being taught. On stop, the video + tap log go to [TeachController].
 *
 * Must be a foreground service of type mediaProjection: Android 10+ refuses screen capture from a
 * background component, and on 14+ startForeground() must come before getMediaProjection().
 */
class TeachRecorderService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val finishing = AtomicBoolean(false)

    private var projection: MediaProjection? = null
    private var recorder: MediaRecorder? = null
    private var display: VirtualDisplay? = null
    private var pfd: ParcelFileDescriptor? = null
    private var videoFile: File? = null
    private var countdown = 0
    private var startedAt = 0L

    // Android 14+: a callback must be registered before createVirtualDisplay(); it also reports the
    // user/system ending the capture (the "Stop sharing" chip).
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() = stopRequested()
    }

    private val countdownTick = object : Runnable {
        override fun run() {
            countdown -= 1
            if (countdown > 0) {
                OverlayBus.state.value = OverlayState.Status("Recording starts in $countdown…")
                handler.postDelayed(this, 1000)
            } else {
                startRecording()
            }
        }
    }

    private val timerTick = object : Runnable {
        override fun run() {
            OverlayBus.state.value = OverlayState.Recording((SystemClock.elapsedRealtime() - startedAt) / 1000)
            handler.postDelayed(this, 1000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Demonstration recording", NotificationManager.IMPORTANCE_LOW))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> begin(intent)
            ACTION_STOP -> {
                logStopTap(intent)
                stopRequested()
            }
        }
        // Never restart after being killed: the projection token is gone by then.
        return START_NOT_STICKY
    }

    private fun begin(intent: Intent) {
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, notification("Starting…"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } catch (e: Exception) {
            return fail("Could not start the recorder: ${e.message}")
        }
        if (projection != null) return // already recording
        finishing.set(false)
        try {
            val data = IntentCompat.getParcelableExtra(intent, EXTRA_RESULT_DATA, Intent::class.java)
                ?: error("missing screen-capture permission result")
            val p = getSystemService(MediaProjectionManager::class.java)
                .getMediaProjection(intent.getIntExtra(EXTRA_RESULT_CODE, 0), data)
                ?: error("no MediaProjection returned")
            p.registerCallback(projectionCallback, handler)
            projection = p
        } catch (e: Exception) {
            return fail("Could not get screen capture: ${e.message}")
        }
        TeachController.onCountdown()
        countdown = COUNTDOWN_SECONDS
        OverlayBus.state.value = OverlayState.Status("Recording starts in $countdown…")
        handler.postDelayed(countdownTick, 1000)
    }

    private fun startRecording() {
        try {
            val (width, height) = screenSize()
            val name = "TeachRec_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()) + ".mp4"
            val dir = File(getExternalFilesDir(Environment.DIRECTORY_MOVIES), "teach").apply { mkdirs() }
            val file = File(dir, name).also { videoFile = it }
            val fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_CREATE or
                ParcelFileDescriptor.MODE_TRUNCATE or ParcelFileDescriptor.MODE_READ_WRITE).also { pfd = it }

            val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder()
            recorder = r
            r.setVideoSource(MediaRecorder.VideoSource.SURFACE) // no audio: no microphone needed
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
            r.setVideoSize(width, height)
            r.setVideoFrameRate(30)
            r.setVideoEncodingBitRate(8_000_000)
            r.setOutputFile(fd.fileDescriptor)
            r.prepare()
            val startCalledUptime = SystemClock.uptimeMillis()
            display = projection!!.createVirtualDisplay(
                "TeachRecorder", width, height, resources.displayMetrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, r.surface, null, handler)
            r.start()
            // Anchor the tap log to the video: event times become ms since this instant.
            val startUptime = SystemClock.uptimeMillis()
            TapLog.begin(startUptime, sessionMeta(name, width, height, startUptime, startCalledUptime))
            startedAt = SystemClock.elapsedRealtime()
            TeachController.onRecordingStarted(this)
            getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification("Recording your demonstration…"))
            handler.post(timerTick)
        } catch (e: Exception) {
            Log.e(TAG, "startRecording failed", e)
            fail("Could not start recording: ${e.message}")
        }
    }

    /** The overlay's Stop tap is logged here: its click event would arrive after the log ends. */
    private fun logStopTap(intent: Intent) {
        if (TapLog.sessionStartUptime() == null) return
        val uptime = intent.getLongExtra(EXTRA_STOP_UPTIME, SystemClock.uptimeMillis())
        val b = intent.getIntArrayExtra(EXTRA_STOP_BOUNDS)
        val bounds = if (b != null && b.size == 4) Rect(b[0], b[1], b[2], b[3]) else null
        TapLog.recordStopTap(uptime, packageName, intent.getStringExtra(EXTRA_STOP_SOURCE) ?: "button", bounds)
    }

    private fun stopRequested() {
        when {
            TapLog.sessionStartUptime() != null -> worker.execute { finishRecording() }
            projection != null -> fail(null) // stopped during the countdown
            else -> shutdown()
        }
    }

    private fun finishRecording() {
        if (!finishing.compareAndSet(false, true)) return
        handler.removeCallbacks(timerTick)
        val log = TapLog.end(SystemClock.uptimeMillis())
        var valid = true
        try {
            recorder?.stop()
        } catch (e: RuntimeException) {
            valid = false // stop() before the first frame: the file is unusable
        }
        release()
        val video = videoFile
        if (video == null || !valid || log == null || !video.isFile || video.length() == 0L) {
            video?.delete()
            TeachController.onRecordingFailed(this, "The recording was too short to use. Please try again.")
        } else {
            val logFile = File(video.parentFile, video.nameWithoutExtension + ".json")
            logFile.writeText(log.toString())
            TeachController.onRecordingFinished(applicationContext, video, logFile)
        }
        handler.post { shutdown() }
    }

    private fun fail(message: String?) {
        TapLog.discard()
        handler.removeCallbacks(countdownTick)
        handler.removeCallbacks(timerTick)
        release()
        videoFile?.delete()
        TeachController.onRecordingFailed(this, message)
        shutdown()
    }

    private fun release() {
        runCatching { recorder?.release() }
        recorder = null
        runCatching { display?.release() }
        display = null
        projection?.let {
            it.unregisterCallback(projectionCallback) // so our own stop() does not re-enter onStop()
            runCatching { it.stop() }
        }
        projection = null
        runCatching { pfd?.close() }
        pfd = null
    }

    private fun shutdown() {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacks(countdownTick)
        if (TapLog.sessionStartUptime() != null) finishRecording() else if (projection != null) fail(null)
        worker.shutdown()
        super.onDestroy()
    }

    private fun sessionMeta(videoName: String, width: Int, height: Int, startUptime: Long, startCalledUptime: Long) =
        JSONObject()
            .put("schemaVersion", TapLog.SCHEMA_VERSION)
            .put("video", JSONObject().put("fileName", videoName).put("width", width).put("height", height))
            .put("device", JSONObject()
                .put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
                .put("sdkInt", Build.VERSION.SDK_INT).put("densityDpi", resources.displayMetrics.densityDpi)
                .put("rotation", getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)?.rotation))
            .put("clock", JSONObject()
                .put("recordingStartUptimeMs", startUptime)
                .put("recorderStartCalledUptimeMs", startCalledUptime)
                .put("recordingStartWallClockMs", System.currentTimeMillis())
                .put("note", "videoTimeMs = eventUptimeMs - recordingStartUptimeMs. Bounds are screen pixels, " +
                    "which equal video pixels (video is recorded at native size)."))
            .put("logger", JSONObject()
                .put("enabledAtStart", WindowWatcherAccessibilityService.isEnabledInSettings(this))
                .put("connectedAtStart", TapLog.serviceConnected))

    /** Native display size, rounded down to even numbers (H.264 requires that). */
    private fun screenSize(): Pair<Int, Int> {
        val b = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        return (b.width() and 1.inv()) to (b.height() and 1.inv())
    }

    private fun notification(text: String): Notification {
        val stop = PendingIntent.getService(this, 0,
            Intent(this, TeachRecorderService::class.java).setAction(ACTION_STOP).putExtra(EXTRA_STOP_SOURCE, "notification"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("Teaching the assistant")
            .setContentText(text)
            .setOngoing(true)
            .addAction(0, "Stop", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val TAG = "TeachRecorder"
        private const val ACTION_START = "com.example.flowlaunchertest.TEACH_START"
        private const val ACTION_STOP = "com.example.flowlaunchertest.TEACH_STOP"
        private const val EXTRA_RESULT_CODE = "result_code"
        private const val EXTRA_RESULT_DATA = "result_data"
        private const val EXTRA_STOP_SOURCE = "stop_source"
        private const val EXTRA_STOP_UPTIME = "stop_uptime"
        private const val EXTRA_STOP_BOUNDS = "stop_bounds"
        private const val CHANNEL_ID = "teach_recording"
        private const val NOTIFICATION_ID = 7
        private const val COUNTDOWN_SECONDS = 3

        fun start(context: Context, resultCode: Int, data: Intent) {
            ContextCompat.startForegroundService(context, Intent(context, TeachRecorderService::class.java)
                .setAction(ACTION_START).putExtra(EXTRA_RESULT_CODE, resultCode).putExtra(EXTRA_RESULT_DATA, data))
        }

        /** [tapUptimeMs]/[tapBounds]: the overlay Stop tap (screen pixels), logged as APP_STOP_TAPPED. */
        fun stop(context: Context, tapUptimeMs: Long? = null, tapBounds: Rect? = null) {
            val intent = Intent(context, TeachRecorderService::class.java).setAction(ACTION_STOP)
            if (tapUptimeMs != null) {
                intent.putExtra(EXTRA_STOP_SOURCE, "button").putExtra(EXTRA_STOP_UPTIME, tapUptimeMs)
                tapBounds?.let { intent.putExtra(EXTRA_STOP_BOUNDS, intArrayOf(it.left, it.top, it.right, it.bottom)) }
            }
            context.startService(intent)
        }
    }
}
