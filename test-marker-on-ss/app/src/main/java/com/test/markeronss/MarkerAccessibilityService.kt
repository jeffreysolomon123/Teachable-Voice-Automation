package com.test.markeronss

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorSpace
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.hardware.HardwareBuffer
import android.os.SystemClock
import android.util.Log
import android.view.Choreographer
import android.view.Display
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume
import kotlin.math.max

private const val TAG = "MarkerA11y"

/**
 * On every click in another app: mark the clicked element, wait until the marker is composited,
 * take a screenshot, remove the marker, save the PNG (marker baked in).
 *
 * The trigger is TYPE_VIEW_CLICKED: one event per click, no touch/move stream is ever delivered
 * to this service. The callback is kept trivial and spaced out by a time gate; everything real
 * runs in a private SupervisorJob scope.
 */
class MarkerAccessibilityService : AccessibilityService() {

    /** [bounds] is null when the clicked element could not be located; [boundsNote] says why. */
    private class Click(val bounds: Rect?, val boundsNote: String?, val eventTimeMs: Long, val pkg: String)

    private sealed interface ShotOutcome {
        class Ok(val buffer: HardwareBuffer, val colorSpace: ColorSpace?) : ShotOutcome
        class Err(val reason: String) : ShotOutcome
    }

    // Isolated from anything else in the app; a failure in one child never cancels the rest.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var consumerJob: Job? = null

    private val queue = Channel<Click>(QUEUE_CAPACITY)
    private val queueOverflow = AtomicInteger(0)

    @Volatile
    private var watching = false

    // Spacing gate, touched only by onAccessibilityEvent and the consumer (cheap volatile ops).
    @Volatile
    private var lastAcceptedMs = 0L

    /** True from the moment a click is accepted until its capture (plus cooldown) has finished. */
    @Volatile
    private var busy = false

    private lateinit var store: CaptureStore
    private var overlay: View? = null
    private var lastShotUptime = 0L

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        store = CaptureStore(applicationContext)
        Heartbeat.start()
        AppState.connected.value = true
        AppState.watching.value = false
    }

    override fun onUnbind(intent: Intent?): Boolean {
        stopWatchingInternal()
        instance = null
        AppState.connected.value = false
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        scope.coroutineContext[Job]?.cancel()
        removeOverlay()
        super.onDestroy()
    }

    override fun onInterrupt() = Unit

    /**
     * Runs on the main thread, once per click. Keep this trivial: filter, time-gate, enqueue.
     * Anything that arrives while a capture is in flight, or sooner than [MIN_CLICK_GAP_MS] after
     * the last accepted click, is discarded with nothing more than a counter bump. For an accepted
     * click the element's bounds are read right here: one IPC into the other app, at most once per
     * gate window. It has to happen now; a click usually changes the screen, and a node fetched
     * after that may be gone (null source), which is what a deferred read kept hitting.
     */
    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.eventType != AccessibilityEvent.TYPE_VIEW_CLICKED) return
        if (!watching || AppState.appInForeground) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg == packageName) return

        val t = SystemClock.uptimeMillis()
        if (busy || t - lastAcceptedMs < MIN_CLICK_GAP_MS) {
            AppState.throttled.incrementAndGet()
            return
        }
        lastAcceptedMs = t
        busy = true

        var bounds: Rect? = null
        var note: String? = null
        try {
            val node = event.source
            if (node == null) {
                note = "click event had no source node"
            } else {
                val r = Rect()
                node.getBoundsInScreen(r)
                if (r.isEmpty) note = "source node bounds were empty $r" else bounds = r
            }
        } catch (e: Throwable) {
            note = "reading source node failed: ${e.message}"
        }
        Log.i(TAG, "click in $pkg bounds=$bounds note=$note")

        if (!queue.trySend(Click(bounds, note, t, pkg)).isSuccess) {
            queueOverflow.incrementAndGet()
            busy = false
        }
    }

    fun startWatching() {
        if (watching) return
        drainQueue()
        queueOverflow.set(0)
        busy = false
        lastAcceptedMs = 0L
        Heartbeat.resetWorstStall()
        watching = true
        consumerJob = scope.launch { consume() }
        AppState.watching.value = true
        AppState.status.value = "Watching for clicks"
    }

    fun stopWatching() {
        stopWatchingInternal()
        AppState.status.value = "Stopped"
    }

    private fun stopWatchingInternal() {
        watching = false
        busy = false
        scope.coroutineContext.cancelChildren()
        consumerJob = null
        drainQueue()
        removeOverlay()
        AppState.watching.value = false
    }

    private fun drainQueue(): Int {
        var n = 0
        while (queue.tryReceive().isSuccess) n++
        return n
    }

    private suspend fun consume() {
        for (click in queue) {
            val overflow = queueOverflow.getAndSet(0)
            if (overflow > 0) {
                Log.w(TAG, "Queue full: $overflow clicks dropped")
                AppState.dropped.update { it + overflow }
            }

            AppState.clicksCaptured.update { it + 1 }
            try {
                processClick(click)
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                Log.e(TAG, "processClick failed", t)
                record(click, fileName = null, failure = "Unexpected error: ${t.message}", width = 0, height = 0)
            } finally {
                // Cooldown timer: clicks stay ignored for a moment after each capture completes.
                if (!currentCoroutineContext().isActive) {
                    busy = false
                } else {
                    delay(COOLDOWN_MS)
                    busy = false
                }
            }
        }
    }

    private suspend fun processClick(click: Click) {
        // takeScreenshot() rejects calls that come too close together.
        val wait = MIN_SHOT_INTERVAL_MS - (SystemClock.uptimeMillis() - lastShotUptime)
        if (wait > 0) delay(wait)

        // No bounds: still take the screenshot (unmarked) rather than lose the click; the gallery
        // shows why the marker is missing.
        var note = click.boundsNote
        val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val marked = click.bounds != null && showMarker(wm, click.bounds)
        if (click.bounds != null && !marked) note = "overlay addView failed"

        val outcome = try {
            if (marked) awaitFrames(SETTLE_FRAMES) // never screenshot in the same frame as addView
            lastShotUptime = SystemClock.uptimeMillis()
            withTimeoutOrNull(SCREENSHOT_TIMEOUT_MS) { requestScreenshot() }
                ?: ShotOutcome.Err("takeScreenshot timed out after ${SCREENSHOT_TIMEOUT_MS}ms")
        } finally {
            removeOverlay() // marker never outlives the screenshot callback
        }

        when (outcome) {
            is ShotOutcome.Err -> record(click, null, outcome.reason, 0, 0)
            is ShotOutcome.Ok -> {
                val latency = SystemClock.uptimeMillis() - click.eventTimeMs
                val name = store.newFileName(System.currentTimeMillis())
                val dims = withContext(Dispatchers.IO) {
                    try {
                        val bitmap = Bitmap.wrapHardwareBuffer(outcome.buffer, outcome.colorSpace)
                        if (bitmap == null) {
                            null
                        } else {
                            store.file(name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                            (bitmap.width to bitmap.height).also { bitmap.recycle() }
                        }
                    } finally {
                        outcome.buffer.close()
                    }
                }
                if (dims == null) {
                    record(click, null, "wrapHardwareBuffer returned null", 0, 0)
                } else {
                    record(click, name, null, dims.first, dims.second, latency, note)
                }
            }
        }
    }

    private suspend fun requestScreenshot(): ShotOutcome = suspendCancellableCoroutine { cont ->
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                val buffer = screenshot.hardwareBuffer
                cont.resume(ShotOutcome.Ok(buffer, screenshot.colorSpace)) { buffer.close() }
            }

            override fun onFailure(errorCode: Int) {
                cont.resume(ShotOutcome.Err(describeError(errorCode)))
            }
        })
    }

    private suspend fun awaitFrames(n: Int) = repeat(n) {
        suspendCancellableCoroutine { cont ->
            Choreographer.getInstance().postFrameCallback { cont.resume(Unit) }
        }
    }

    /** Overlay window sized to the clicked element (plus a margin), placed on top of it. */
    private fun showMarker(wm: WindowManager, bounds: Rect): Boolean {
        val density = resources.displayMetrics.density
        val pad = (MARKER_PAD_DP * density).toInt()
        val minSide = (MIN_MARKER_DP * density).toInt()
        val w = max(bounds.width(), minSide) + 2 * pad
        val h = max(bounds.height(), minSide) + 2 * pad
        val lp = WindowManager.LayoutParams(
            w, h,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            // Screen-absolute coordinates, unaffected by a display cutout.
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            // Centered on the element.
            x = bounds.centerX() - w / 2
            y = bounds.centerY() - h / 2
        }
        val view = MarkerView(this)
        return try {
            wm.addView(view, lp)
            overlay = view
            true
        } catch (t: Throwable) {
            Log.e(TAG, "addView failed", t)
            false
        }
    }

    private fun removeOverlay() {
        val v = overlay ?: return
        overlay = null
        try {
            (getSystemService(Context.WINDOW_SERVICE) as WindowManager).removeViewImmediate(v)
        } catch (t: Throwable) {
            Log.w(TAG, "removeView failed", t)
        }
    }

    private suspend fun record(
        click: Click, fileName: String?, failure: String?, width: Int, height: Int,
        latencyMs: Long = SystemClock.uptimeMillis() - click.eventTimeMs,
        note: String? = null,
    ) {
        val capture = Capture(
            timeMs = System.currentTimeMillis(), pkg = click.pkg,
            fileName = fileName, failure = failure,
            latencyMs = latencyMs, width = width, height = height, note = note,
        )
        withContext(Dispatchers.IO) { store.append(capture) }
        if (capture.ok) AppState.saved.update { it + 1 } else AppState.failed.update { it + 1 }
        AppState.status.value = if (capture.ok) {
            "Saved click in ${capture.pkg} (${capture.latencyMs}ms)" + if (note != null) " [no marker]" else ""
        } else {
            "FAILED click in ${capture.pkg}: $failure"
        }
        AppState.capturesVersion.update { it + 1 }
    }

    private fun describeError(code: Int): String = when (code) {
        ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "BLOCKED: FLAG_SECURE screen, screenshot not allowed"
        ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "takeScreenshot rejected: called too soon after the previous one"
        ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "takeScreenshot rejected: no accessibility screenshot access"
        ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "takeScreenshot rejected: invalid display"
        ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "takeScreenshot internal error"
        else -> "takeScreenshot failed, unknown error code $code"
    }

    /** Red outline around the clicked element plus a centre dot, on a light tint. */
    private class MarkerView(context: Context) : View(context) {
        private val density = resources.displayMetrics.density
        private val tint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(45, 255, 0, 0) }
        private val halo = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            style = Paint.Style.STROKE
            strokeWidth = 7f * density
        }
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.RED
            style = Paint.Style.STROKE
            strokeWidth = 3.5f * density
        }
        private val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.RED }
        private val rect = RectF()

        override fun onDraw(canvas: Canvas) {
            val inset = halo.strokeWidth / 2f + 1f * density
            rect.set(inset, inset, width - inset, height - inset)
            val r = 10f * density
            canvas.drawRoundRect(rect, r, r, tint)
            canvas.drawRoundRect(rect, r, r, halo)
            canvas.drawRoundRect(rect, r, r, ring)
            canvas.drawCircle(width / 2f, height / 2f, 5f * density, halo)
            canvas.drawCircle(width / 2f, height / 2f, 4f * density, dot)
        }
    }

    companion object {
        const val MARKER_PAD_DP = 6
        const val MIN_MARKER_DP = 36

        /** Vsyncs to wait after addView before screenshotting. Raise if a marker is ever missing. */
        const val SETTLE_FRAMES = 2

        /** Minimum spacing between accepted clicks; anything sooner is discarded in the callback. */
        const val MIN_CLICK_GAP_MS = 700L

        /** Extra quiet time after a capture finishes before the next click is accepted. */
        const val COOLDOWN_MS = 300L
        const val QUEUE_CAPACITY = 8
        const val MIN_SHOT_INTERVAL_MS = 400L
        const val SCREENSHOT_TIMEOUT_MS = 3000L

        @Volatile
        var instance: MarkerAccessibilityService? = null
    }
}
