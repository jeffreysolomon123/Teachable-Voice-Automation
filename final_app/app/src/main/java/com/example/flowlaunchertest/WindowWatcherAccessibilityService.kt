package com.example.flowlaunchertest

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ComponentName
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.inputmethod.EditorInfo
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * Device-side capabilities for visual automation. This service never reads the accessibility tree
 * (canRetrieveWindowContent=false in its config); it only reports window-state changes by package,
 * takes screenshots, injects coordinate gestures, presses BACK, and commits text into whatever field
 * currently has input focus (the backend taps that field visually first).
 */
class WindowWatcherAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "WindowWatcher"
        private const val MIN_SCREENSHOT_INTERVAL_MS = 1100L // system limit is ~1 per second
        private const val INPUT_READY_TIMEOUT_MS = 2000L

        /** The connected instance, or null when the service isn't running. */
        @Volatile
        var instance: WindowWatcherAccessibilityService? = null
            private set

        val isConnected: Boolean get() = instance != null

        fun isEnabledInSettings(context: Context): Boolean {
            val me = ComponentName(context, WindowWatcherAccessibilityService::class.java)
            val enabled = Settings.Secure.getString(
                context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            ) ?: return false
            val splitter = TextUtils.SimpleStringSplitter(':').apply { setString(enabled) }
            return splitter.any { ComponentName.unflattenFromString(it) == me }
        }
    }

    enum class TypeResult { OK, NO_FOCUSED_FIELD, UNSUPPORTED }

    private var watchedPkg: String? = null
    private var lastWindowEventAt = 0L
    private var windowEventSeen: CompletableDeferred<Unit>? = null
    private var lastScreenshotAt = 0L

    override fun onServiceConnected() {
        instance = this
        Log.i(TAG, "Accessibility service connected (SDK ${Build.VERSION.SDK_INT})")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = watchedPkg ?: return
        if (event.packageName?.toString() != pkg) return
        lastWindowEventAt = SystemClock.elapsedRealtime()
        windowEventSeen?.complete(Unit)
    }

    // ------------------------------------------------------------------ app launch

    /**
     * Launches [pkg] and waits until its windows stop changing for [settleMs] (or [timeoutMs]
     * passes). Returns false if the app isn't installed.
     */
    suspend fun launchAndSettle(pkg: String, settleMs: Long, timeoutMs: Long = 10_000L): Boolean {
        val intent = packageManager.getLaunchIntentForPackage(pkg)?.apply {
            addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        } ?: return false
        watchedPkg = pkg
        windowEventSeen = CompletableDeferred()
        lastWindowEventAt = 0L
        startActivity(intent)
        try {
            val seen = withTimeoutOrNull(timeoutMs) { windowEventSeen?.await() }
            if (seen == null) {
                Log.w(TAG, "No window event from $pkg within ${timeoutMs}ms; continuing after settle delay")
                delay(settleMs)
                return true
            }
            // Debounce: wait until no window-state change for settleMs.
            while (SystemClock.elapsedRealtime() - lastWindowEventAt < settleMs) {
                delay(200)
            }
            return true
        } finally {
            watchedPkg = null
            windowEventSeen = null
        }
    }

    // ------------------------------------------------------------------ screenshots

    /** Full-resolution screenshot of the default display, or null on failure. */
    suspend fun captureScreenshot(): Bitmap? {
        val wait = lastScreenshotAt + MIN_SCREENSHOT_INTERVAL_MS - SystemClock.elapsedRealtime()
        if (wait > 0) delay(wait)
        repeat(3) { attempt ->
            val result = takeOneScreenshot()
            lastScreenshotAt = SystemClock.elapsedRealtime()
            if (result.first != null) return result.first
            // ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT: back off and retry.
            if (result.second == ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT) delay(MIN_SCREENSHOT_INTERVAL_MS)
            else if (attempt == 2) return null
            else delay(300)
        }
        return null
    }

    private suspend fun takeOneScreenshot(): Pair<Bitmap?, Int> = suspendCancellableCoroutine { cont ->
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val bitmap = try {
                    result.hardwareBuffer.use { buffer ->
                        Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to convert screenshot", e)
                    null
                }
                if (cont.isActive) cont.resume(bitmap to 0)
            }

            override fun onFailure(errorCode: Int) {
                Log.w(TAG, "takeScreenshot failed with error code $errorCode")
                if (cont.isActive) cont.resume(null to errorCode)
            }
        })
    }

    // ------------------------------------------------------------------ gestures (display pixels)

    suspend fun tap(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        return dispatch(GestureDescription.StrokeDescription(path, 0, 60))
    }

    suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        return dispatch(GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(50, 5000)))
    }

    private suspend fun dispatch(stroke: GestureDescription.StrokeDescription): Boolean =
        suspendCancellableCoroutine { cont ->
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            val accepted = dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    if (cont.isActive) cont.resume(true)
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    Log.w(TAG, "Gesture cancelled")
                    if (cont.isActive) cont.resume(false)
                }
            }, null)
            if (!accepted && cont.isActive) cont.resume(false)
        }

    fun back(): Boolean = performGlobalAction(GLOBAL_ACTION_BACK)

    // ------------------------------------------------------------------ text input (no tree)

    /**
     * Replaces the content of the currently focused text field with [text] via the accessibility
     * InputMethod (Android 13+, flagInputMethodEditor). No node lookup: the text goes to whatever
     * editor holds input focus, which the backend selected by tapping it visually.
     */
    suspend fun typeText(text: String, submit: Boolean): TypeResult {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return TypeResult.UNSUPPORTED
        val ime = inputMethod ?: return TypeResult.UNSUPPORTED
        val started = withTimeoutOrNull(INPUT_READY_TIMEOUT_MS) {
            while (!ime.currentInputStarted || ime.currentInputConnection == null) delay(100)
            true
        }
        val ic = ime.currentInputConnection
        if (started == null || ic == null) {
            Log.w(TAG, "No focused editable field (input not started)")
            return TypeResult.NO_FOCUSED_FIELD
        }
        ic.performContextMenuAction(android.R.id.selectAll)
        ic.commitText(text, 1, null)
        if (submit) {
            val action = (ime.currentInputEditorInfo?.imeOptions ?: 0) and EditorInfo.IME_MASK_ACTION
            ic.performEditorAction(if (action == EditorInfo.IME_ACTION_NONE || action == EditorInfo.IME_ACTION_UNSPECIFIED)
                EditorInfo.IME_ACTION_SEARCH else action)
        }
        Log.i(TAG, "Committed ${text.length} chars (submit=$submit)")
        return TypeResult.OK
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onDestroy() {
        instance = null
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }
}
