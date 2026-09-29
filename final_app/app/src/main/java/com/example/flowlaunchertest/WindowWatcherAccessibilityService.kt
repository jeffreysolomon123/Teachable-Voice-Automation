package com.example.flowlaunchertest

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class WindowWatcherAccessibilityService : AccessibilityService() {

    data class WatchRequest(
        val pkg: String,
        val appName: String,
        // Makes repeat launches of the same app distinct StateFlow values.
        val requestedAt: Long = SystemClock.elapsedRealtime(),
    )

    companion object {
        private const val TAG = "WindowWatcher"
        private const val SETTLE_MS = 3000L
        private const val LAUNCH_TIMEOUT_MS = 8000L
        // Gives the overlay a moment to hide before the screenshot is taken.
        private const val HIDE_OVERLAY_BEFORE_CAPTURE_MS = 250L

        /** Set by MainActivity right before launching the target app; null = not watching. */
        val watched = MutableStateFlow<WatchRequest?>(null)

        /** True while the system has this service bound. */
        @Volatile
        var isConnected = false
            private set

        fun isEnabledInSettings(context: Context): Boolean {
            val me = ComponentName(context, WindowWatcherAccessibilityService::class.java)
            val enabled = Settings.Secure.getString(
                context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            ) ?: return false
            val splitter = TextUtils.SimpleStringSplitter(':').apply { setString(enabled) }
            return splitter.any { ComponentName.unflattenFromString(it) == me }
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private var scope: CoroutineScope? = null
    private var firstEventSeenFor: WatchRequest? = null
    private val settleRunnable = Runnable { onSettled() }
    private val timeoutRunnable = Runnable { onLaunchTimeout() }

    override fun onServiceConnected() {
        isConnected = true
        Log.i(TAG, "Accessibility service connected")
        scope = MainScope().also { s ->
            s.launch { watched.collect { onWatchChanged(it) } }
        }
    }

    private fun onWatchChanged(req: WatchRequest?) {
        handler.removeCallbacks(settleRunnable)
        handler.removeCallbacks(timeoutRunnable)
        if (req != null) {
            Log.i(TAG, "Watching ${req.pkg}; timeout in ${LAUNCH_TIMEOUT_MS}ms")
            handler.postDelayed(timeoutRunnable, LAUNCH_TIMEOUT_MS)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val req = watched.value ?: return
        if (event.packageName?.toString() != req.pkg) return

        Log.d(TAG, "Window state changed: ${event.packageName} / ${event.className}")
        if (firstEventSeenFor !== req) {
            firstEventSeenFor = req
            handler.removeCallbacks(timeoutRunnable)
            OverlayBus.state.value = OverlayState.Waiting(req.appName)
        }
        // Debounce: restart the settle timer on every matching event.
        handler.removeCallbacks(settleRunnable)
        handler.postDelayed(settleRunnable, SETTLE_MS)
    }

    private fun onLaunchTimeout() {
        val req = watched.value ?: return
        Log.w(TAG, "No window-state event from ${req.pkg} within ${LAUNCH_TIMEOUT_MS}ms")
        OverlayBus.state.value = OverlayState.TimedOut(req.appName)
        watched.value = null
    }

    private fun onSettled() {
        val req = watched.value ?: return
        Log.i(TAG, "${req.pkg} settled (${SETTLE_MS}ms without window changes); taking screenshot")
        watched.value = null // one check per launch
        OverlayBus.capturing.value = true
        handler.postDelayed({ capture() }, HIDE_OVERLAY_BEFORE_CAPTURE_MS)
    }

    private fun capture() {
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val bitmap = try {
                    result.hardwareBuffer.use { buffer ->
                        // Copy to a software bitmap so it outlives the buffer and can be scaled/compressed.
                        Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                            ?.copy(Bitmap.Config.ARGB_8888, false)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to convert screenshot", e)
                    null
                }
                OverlayBus.capturing.value = false
                if (bitmap == null) {
                    OverlayBus.state.value = OverlayState.Blocked("check failed (screenshot conversion failed)")
                    return
                }
                Log.i(TAG, "Screenshot captured: ${bitmap.width}x${bitmap.height}")
                OverlayBus.state.value = OverlayState.Checking
                if (OverlayBus.screenshots.subscriptionCount.value == 0) {
                    Log.e(TAG, "OverlayService is not running; nobody to run the LLM check")
                }
                OverlayBus.screenshots.tryEmit(bitmap)
            }

            override fun onFailure(errorCode: Int) {
                Log.e(TAG, "takeScreenshot failed with error code $errorCode")
                OverlayBus.capturing.value = false
                OverlayBus.state.value = OverlayState.Blocked("check failed (screenshot error $errorCode)")
            }
        })
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onDestroy() {
        isConnected = false
        handler.removeCallbacksAndMessages(null)
        scope?.cancel()
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }
}
