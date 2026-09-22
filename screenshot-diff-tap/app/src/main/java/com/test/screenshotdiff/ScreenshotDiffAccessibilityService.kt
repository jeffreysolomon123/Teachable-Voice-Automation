package com.test.screenshotdiff

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.SystemClock
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

class ScreenshotDiffAccessibilityService : AccessibilityService() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var rollingJob: kotlinx.coroutines.Job? = null

    @Volatile
    private var currentBefore: Bitmap? = null
    private val beforeLock = Any()
    private var tapIdCounter = 0

    private var lastTriggerAt = 0L
    private val debounceLock = Any()

    override fun onServiceConnected() {
        super.onServiceConnected()

        // Runs unconditionally, independent of watching state — the same hang-detection
        // signal used in the earlier overlay test.
        serviceScope.launch {
            while (isActive) {
                kotlinx.coroutines.delay(1000)
                TapCaptureState.heartbeat.update { it + 1 }
            }
        }

        serviceScope.launch {
            TapCaptureState.isWatching.collect { watching ->
                if (watching) startRollingCapture() else stopRollingCapture()
            }
        }
    }

    private fun startRollingCapture() {
        if (rollingJob?.isActive == true) return
        rollingJob = serviceScope.launch {
            while (isActive) {
                try {
                    val bmp = captureScreenshotScaledTo(ROLLING_WIDTH)
                    if (bmp != null) {
                        val old = synchronized(beforeLock) {
                            val prev = currentBefore
                            currentBefore = bmp
                            prev
                        }
                        old?.recycle()
                    }
                } catch (t: Throwable) {
                    // Skip this frame; keep the loop alive no matter what.
                }
                kotlinx.coroutines.delay(300)
            }
        }
    }

    private fun stopRollingCapture() {
        rollingJob?.cancel()
        rollingJob = null
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val type = event?.eventType ?: return
        if (type !in TRIGGER_EVENT_TYPES) return
        if (!TapCaptureState.isWatching.value) return

        // Many apps (WebViews, hybrid/custom-touch UIs) never fire TYPE_VIEW_CLICKED, so we
        // also trigger on broader signals and debounce so one tap's cascade of events (and
        // background animation) doesn't produce multiple captures.
        val now = SystemClock.elapsedRealtime()
        synchronized(debounceLock) {
            if (now - lastTriggerAt < DEBOUNCE_MS) return
            lastTriggerAt = now
        }

        val before = synchronized(beforeLock) { currentBefore } ?: return
        val tapId = tapIdCounter++

        serviceScope.launch {
            try {
                withTimeoutOrNull(TAP_PROCESS_TIMEOUT_MS) {
                    processTap(tapId, before)
                }
            } catch (t: Throwable) {
                // Skip this tap's region rather than block or retry.
            }
        }
    }

    private suspend fun processTap(tapId: Int, before: Bitmap) {
        val fullAfter = captureScreenshotFull() ?: return
        try {
            val displayBitmap = downscale(fullAfter, DISPLAY_WIDTH)
            val diff = try {
                DiffEngine.computeDiff(before, fullAfter, displayBitmap.width, displayBitmap.height)
            } catch (t: Throwable) {
                null
            }

            TapCaptureState.tapCount.update { it + 1 }
            TapCaptureState.capturedTaps.update { list ->
                list + CapturedTap(
                    id = tapId,
                    displayBitmap = displayBitmap,
                    region = diff?.region,
                    confidence = diff?.confidence ?: "n/a",
                    timestampMs = System.currentTimeMillis()
                )
            }
        } finally {
            fullAfter.recycle()
        }
    }

    private suspend fun captureScreenshotScaledTo(targetWidth: Int): Bitmap? {
        val full = suspendScreenshot() ?: return null
        return try {
            downscale(full, targetWidth)
        } finally {
            full.recycle()
        }
    }

    private suspend fun captureScreenshotFull(): Bitmap? = suspendScreenshot()

    private fun downscale(bitmap: Bitmap, targetWidth: Int): Bitmap {
        val width = targetWidth.coerceAtMost(bitmap.width)
        val scale = width.toFloat() / bitmap.width
        val height = (bitmap.height * scale).toInt().coerceAtLeast(1)
        return Bitmap.createScaledBitmap(bitmap, width, height, true)
    }

    private suspend fun suspendScreenshot(): Bitmap? = suspendCancellableCoroutine { cont ->
        try {
            takeScreenshot(
                Display.DEFAULT_DISPLAY,
                mainExecutor,
                object : TakeScreenshotCallback {
                    override fun onSuccess(result: ScreenshotResult) {
                        val bitmap = try {
                            val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                            result.hardwareBuffer.close()
                            val software = hw?.copy(Bitmap.Config.ARGB_8888, false)
                            hw?.recycle()
                            software
                        } catch (t: Throwable) {
                            null
                        }
                        if (cont.isActive) cont.resume(bitmap)
                    }

                    override fun onFailure(errorCode: Int) {
                        if (cont.isActive) cont.resume(null)
                    }
                }
            )
        } catch (t: Throwable) {
            if (cont.isActive) cont.resume(null)
        }
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        super.onDestroy()
        stopRollingCapture()
        serviceScope.cancel()
    }

    companion object {
        private const val ROLLING_WIDTH = 150
        private const val DISPLAY_WIDTH = 720
        private const val TAP_PROCESS_TIMEOUT_MS = 2000L
        private const val DEBOUNCE_MS = 500L

        private val TRIGGER_EVENT_TYPES = setOf(
            AccessibilityEvent.TYPE_VIEW_CLICKED,
            AccessibilityEvent.TYPE_VIEW_LONG_CLICKED,
            AccessibilityEvent.TYPE_VIEW_SELECTED,
            AccessibilityEvent.TYPE_VIEW_SCROLLED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
        )
    }
}
