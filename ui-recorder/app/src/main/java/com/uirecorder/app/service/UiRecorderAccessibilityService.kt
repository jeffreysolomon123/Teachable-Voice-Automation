package com.uirecorder.app.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.ScreenshotResult
import android.accessibilityservice.AccessibilityService.TakeScreenshotCallback
import android.accessibilityservice.AccessibilityServiceInfo
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Build
import android.provider.Settings
import android.util.Log
import android.view.Display
import android.view.InputDevice
import android.view.MotionEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.annotation.RequiresApi
import com.uirecorder.app.data.AccessibilityFingerprint
import com.uirecorder.app.data.ActionCaptureRepository
import com.uirecorder.app.data.Bounds
import com.uirecorder.app.data.CapturedAction
import com.uirecorder.app.data.NodeInfo
import com.uirecorder.app.data.RecorderRepository
import com.uirecorder.app.data.TapCaptureRepository
import com.uirecorder.app.data.TapStepRecord
import com.uirecorder.app.data.TouchPoint
import com.uirecorder.app.data.UiEvent
import com.uirecorder.app.ocr.OcrProcessor
import com.uirecorder.app.util.ScreenshotStore
import java.util.ArrayDeque
import java.util.concurrent.Executors
import kotlin.math.abs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch

class UiRecorderAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "UiRecorderService"
        private const val TOUCH_MATCH_WINDOW_MS = 200L
        private const val TOUCH_BUFFER_MAX_SIZE = 50
        private const val ACTION_SCREENSHOT_DIR_NAME = "action_capture_screenshots"

        // com.android.systemui is always excluded from active-app tracking; the IME package is
        // resolved at runtime via Settings.Secure.DEFAULT_INPUT_METHOD since it varies by device
        // (Gboard vs Samsung Keyboard vs ...), unlike the fixed value implied by "latin".
        private const val SYSTEM_UI_PACKAGE = "com.android.systemui"
    }

    private data class OcrJob(val stepIndex: Int, val bitmap: Bitmap, val touchPoint: TouchPoint?)

    // Buffered text for the field currently being typed into, in per-action-capture mode. Keyed
    // by resourceId when present, else by the source node's identity hash (captured before the
    // node is recycled — AccessibilityNodeInfo.hashCode()/equals() are based on window+view id,
    // not object identity, so this stays stable across separate event dispatches for the same
    // underlying view), else by packageName as a last resort so apps with zero accessibility
    // node data (Amazon, Zomato) still get one committed record per field instead of none.
    private data class PendingTextField(
        val key: Any,
        val fingerprint: AccessibilityFingerprint?,
        val packageName: String,
        val latestText: String
    )

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val ocrJobChannel = Channel<OcrJob>(Channel.UNLIMITED)
    private val ocrProcessor = OcrProcessor()
    private val screenshotExecutor = Executors.newSingleThreadExecutor()
    private val touchPointBuffer = TouchPointBuffer()

    // Per-action-capture mode state. Reset at the start of every new ActionCaptureRepository
    // session (see the isRecording-transition check in onAccessibilityEvent).
    private var actionCaptureSessionActive = false
    private var pendingTextField: PendingTextField? = null
    private val lastScrollPosition = HashMap<Any, Int>()
    private var currentActiveApp: String? = null

    override fun onCreate() {
        super.onCreate()
        serviceScope.launch {
            for (job in ocrJobChannel) {
                processOcrJob(job)
            }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        // Raw motion event capture is requested ONLY when the user explicitly opts in via the
        // UI (RecorderRepository.rawTouchCaptureEnabled), never just because the accessibility
        // service itself is enabled. It has been observed to freeze touch input system-wide on
        // at least one real device (MIUI/HyperOS) — this reacts live so the user can also turn
        // it back off from the app without needing touch input to work.
        serviceScope.launch {
            RecorderRepository.rawTouchCaptureEnabled.collect { enabled ->
                applyRawTouchCapture(enabled)
            }
        }
    }

    private fun applyRawTouchCapture(enabled: Boolean) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return
        try {
            val info = serviceInfo ?: return
            if (enabled) {
                info.motionEventSources = InputDevice.SOURCE_TOUCHSCREEN
                info.flags = info.flags or AccessibilityServiceInfo.FLAG_SEND_MOTION_EVENTS
            } else {
                info.motionEventSources = 0
                info.flags = info.flags and AccessibilityServiceInfo.FLAG_SEND_MOTION_EVENTS.inv()
            }
            serviceInfo = info
        } catch (e: Exception) {
            Log.e(TAG, "Failed to update raw touch capture service info", e)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (!RecorderRepository.isRecording.value) return

        // Recording our own UI would create a feedback loop: appending an event recomposes the
        // live log, which fires more events. This tool is for observing *other* apps only.
        if (event.packageName?.toString() == packageName) return

        try {
            val sourceNode = event.source
            val nodeInfo = sourceNode?.let { buildNodeInfo(it, ancestorLevels = 3) }

            var fullTreeSnapshot: List<NodeInfo>? = null
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
                RecorderRepository.fullTreeCaptureEnabled.value
            ) {
                val root = rootInActiveWindow
                if (root != null) {
                    fullTreeSnapshot = captureTree(root)
                    root.recycle()
                }
            }

            val isScroll = event.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED
            val isTextChange = event.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED

            val uiEvent = UiEvent(
                eventType = eventTypeToString(event.eventType),
                packageName = event.packageName?.toString() ?: "",
                timestampWallClockMs = System.currentTimeMillis(),
                timestampMonotonicMs = event.eventTime,
                eventText = event.text?.filterNotNull()?.map { it.toString() } ?: emptyList(),
                scrollDeltaX = if (isScroll && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) event.scrollDeltaX else null,
                scrollDeltaY = if (isScroll && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) event.scrollDeltaY else null,
                scrollX = if (isScroll) event.scrollX else null,
                scrollY = if (isScroll) event.scrollY else null,
                beforeText = if (isTextChange) event.beforeText?.toString() else null,
                addedCount = if (isTextChange) event.addedCount else null,
                removedCount = if (isTextChange) event.removedCount else null,
                source = nodeInfo,
                fullTreeSnapshot = fullTreeSnapshot
            )

            RecorderRepository.addEvent(uiEvent)

            if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED ||
                event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED
            ) {
                handleTapCapture(event, nodeInfo)
            }

            if (ActionCaptureRepository.isRecording.value) {
                handlePerActionCapture(event, sourceNode)
            } else {
                actionCaptureSessionActive = false
            }

            sourceNode?.recycle()
        } catch (e: Exception) {
            // AccessibilityNodeInfo can go stale mid-read if the source window closes; drop
            // this one event rather than taking down the whole recording session.
            Log.e(TAG, "Failed to process accessibility event", e)
        }
    }

    @RequiresApi(Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
    override fun onMotionEvent(event: MotionEvent) {
        if (!RecorderRepository.isRecording.value) return
        if (!RecorderRepository.rawTouchCaptureEnabled.value) return
        if (event.source != InputDevice.SOURCE_TOUCHSCREEN) return

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP -> {
                touchPointBuffer.record(TouchPoint(event.x, event.y, event.eventTime))
            }
        }
    }

    override fun onInterrupt() {
        // No-op: nothing to tear down between event batches.
    }

    override fun onDestroy() {
        super.onDestroy()
        ocrJobChannel.close()
        serviceScope.cancel()
        screenshotExecutor.shutdown()
    }

    private fun handleTapCapture(event: AccessibilityEvent, nodeInfo: NodeInfo?) {
        val stepIndex = TapCaptureRepository.nextStepIndex()
        val touchCaptureSupported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE &&
            RecorderRepository.rawTouchCaptureEnabled.value
        val touchPoint = if (touchCaptureSupported) {
            touchPointBuffer.findNearest(event.eventTime, TOUCH_MATCH_WINDOW_MS)
        } else {
            null
        }

        val record = TapStepRecord(
            stepIndex = stepIndex,
            timestampWallClockMs = System.currentTimeMillis(),
            packageName = event.packageName?.toString() ?: "",
            eventType = eventTypeToString(event.eventType),
            accessibilityNode = nodeInfo,
            eventText = event.text?.filterNotNull()?.map { it.toString() } ?: emptyList(),
            touchPoint = touchPoint,
            touchCaptureSupported = touchCaptureSupported
        )
        TapCaptureRepository.addStep(record)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            captureScreenshotForStep(stepIndex, touchPoint)
        }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun captureScreenshotForStep(stepIndex: Int, touchPoint: TouchPoint?) {
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            screenshotExecutor,
            object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    val bitmap = decodeScreenshot(stepIndex, screenshot)
                    if (bitmap == null) {
                        TapCaptureRepository.updateStep(stepIndex) {
                            it.copy(screenshotBlocked = true, screenshotBlockedReason = "decode_failed")
                        }
                        return
                    }

                    val blank = ScreenshotStore.looksBlank(bitmap)
                    val path = if (blank) null else ScreenshotStore.save(applicationContext, stepIndex, bitmap)

                    TapCaptureRepository.updateStep(stepIndex) {
                        it.copy(
                            screenshotPath = path,
                            screenshotBlocked = blank,
                            screenshotBlockedReason = if (blank) "blank_pixels" else null
                        )
                    }

                    if (blank) {
                        bitmap.recycle()
                    } else {
                        ocrJobChannel.trySend(OcrJob(stepIndex, bitmap, touchPoint))
                    }
                }

                override fun onFailure(errorCode: Int) {
                    // ERROR_TAKE_SCREENSHOT_SECURE_WINDOW is a real signal (FLAG_SECURE).
                    // ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT is NOT — it just means two click
                    // events fired close enough together to hit takeScreenshot()'s rate limit.
                    val reason = screenshotErrorReason(errorCode)
                    Log.w(TAG, "takeScreenshot failed for step $stepIndex: errorCode=$errorCode ($reason)")
                    TapCaptureRepository.updateStep(stepIndex) {
                        it.copy(screenshotBlocked = true, screenshotBlockedReason = reason)
                    }
                }
            }
        )
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun screenshotErrorReason(errorCode: Int): String = when (errorCode) {
        ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "secure_window"
        ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "rate_limited"
        ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "invalid_display"
        ERROR_TAKE_SCREENSHOT_INVALID_WINDOW -> "invalid_window"
        ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "no_accessibility_access"
        ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "internal_error"
        else -> "unknown_error_$errorCode"
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun decodeScreenshot(stepIndex: Int, screenshot: ScreenshotResult): Bitmap? {
        return try {
            val hardwareBitmap = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
            val software = hardwareBitmap?.copy(Bitmap.Config.ARGB_8888, false)
            hardwareBitmap?.recycle()
            screenshot.hardwareBuffer.close()
            software
        } catch (e: Exception) {
            Log.e(TAG, "Failed to decode screenshot buffer for step $stepIndex", e)
            null
        }
    }

    private suspend fun processOcrJob(job: OcrJob) {
        try {
            val result = ocrProcessor.process(job.bitmap, job.touchPoint)
            TapCaptureRepository.updateStep(job.stepIndex) {
                it.copy(
                    ocrCandidates = result.candidates,
                    ocrMethodUsed = result.methodUsed,
                    ocrProcessed = true
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "OCR failed for step ${job.stepIndex}", e)
            TapCaptureRepository.updateStep(job.stepIndex) { it.copy(ocrProcessed = true) }
        } finally {
            job.bitmap.recycle()
        }
    }

    private fun buildNodeInfo(node: AccessibilityNodeInfo, ancestorLevels: Int, depth: Int? = null): NodeInfo {
        val rect = Rect()
        node.getBoundsInScreen(rect)

        return NodeInfo(
            resourceId = node.viewIdResourceName,
            className = node.className?.toString(),
            text = node.text?.toString(),
            contentDescription = node.contentDescription?.toString(),
            hintText = node.hintText?.toString(),
            bounds = Bounds(rect.left, rect.top, rect.right, rect.bottom),
            isClickable = node.isClickable,
            isCheckable = node.isCheckable,
            isChecked = node.isChecked,
            isEnabled = node.isEnabled,
            isFocused = node.isFocused,
            isScrollable = node.isScrollable,
            isEditable = node.isEditable,
            isPassword = node.isPassword,
            isLongClickable = node.isLongClickable,
            childCount = node.childCount,
            depth = depth,
            ancestors = if (ancestorLevels > 0) captureAncestors(node, ancestorLevels) else null
        )
    }

    private fun captureAncestors(node: AccessibilityNodeInfo, maxLevels: Int): List<NodeInfo> {
        val ancestors = mutableListOf<NodeInfo>()
        var current = node.parent
        var levels = 0
        while (current != null && levels < maxLevels) {
            ancestors.add(
                NodeInfo(
                    resourceId = current.viewIdResourceName,
                    className = current.className?.toString()
                )
            )
            val next = current.parent
            current.recycle()
            current = next
            levels++
        }
        return ancestors
    }

    private fun captureTree(root: AccessibilityNodeInfo): List<NodeInfo> {
        val flattened = mutableListOf<NodeInfo>()

        fun walk(node: AccessibilityNodeInfo, depth: Int) {
            flattened.add(buildNodeInfo(node, ancestorLevels = 0, depth = depth))
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                walk(child, depth + 1)
                child.recycle()
            }
        }

        walk(root, 0)
        return flattened
    }

    private fun eventTypeToString(type: Int): String = when (type) {
        AccessibilityEvent.TYPE_VIEW_CLICKED -> "VIEW_CLICKED"
        AccessibilityEvent.TYPE_VIEW_LONG_CLICKED -> "VIEW_LONG_CLICKED"
        AccessibilityEvent.TYPE_VIEW_SELECTED -> "VIEW_SELECTED"
        AccessibilityEvent.TYPE_VIEW_FOCUSED -> "VIEW_FOCUSED"
        AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> "VIEW_TEXT_CHANGED"
        AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> "WINDOW_STATE_CHANGED"
        AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED -> "NOTIFICATION_STATE_CHANGED"
        AccessibilityEvent.TYPE_VIEW_HOVER_ENTER -> "VIEW_HOVER_ENTER"
        AccessibilityEvent.TYPE_VIEW_HOVER_EXIT -> "VIEW_HOVER_EXIT"
        AccessibilityEvent.TYPE_TOUCH_EXPLORATION_GESTURE_START -> "TOUCH_EXPLORATION_GESTURE_START"
        AccessibilityEvent.TYPE_TOUCH_EXPLORATION_GESTURE_END -> "TOUCH_EXPLORATION_GESTURE_END"
        AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> "WINDOW_CONTENT_CHANGED"
        AccessibilityEvent.TYPE_VIEW_SCROLLED -> "VIEW_SCROLLED"
        AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED -> "VIEW_TEXT_SELECTION_CHANGED"
        AccessibilityEvent.TYPE_ANNOUNCEMENT -> "ANNOUNCEMENT"
        AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED -> "VIEW_ACCESSIBILITY_FOCUSED"
        AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED -> "VIEW_ACCESSIBILITY_FOCUS_CLEARED"
        AccessibilityEvent.TYPE_VIEW_TEXT_TRAVERSED_AT_MOVEMENT_GRANULARITY -> "VIEW_TEXT_TRAVERSED_AT_MOVEMENT_GRANULARITY"
        AccessibilityEvent.TYPE_GESTURE_DETECTION_START -> "GESTURE_DETECTION_START"
        AccessibilityEvent.TYPE_GESTURE_DETECTION_END -> "GESTURE_DETECTION_END"
        AccessibilityEvent.TYPE_TOUCH_INTERACTION_START -> "TOUCH_INTERACTION_START"
        AccessibilityEvent.TYPE_TOUCH_INTERACTION_END -> "TOUCH_INTERACTION_END"
        AccessibilityEvent.TYPE_WINDOWS_CHANGED -> "WINDOWS_CHANGED"
        AccessibilityEvent.TYPE_VIEW_CONTEXT_CLICKED -> "VIEW_CONTEXT_CLICKED"
        AccessibilityEvent.TYPE_ASSIST_READING_CONTEXT -> "ASSIST_READING_CONTEXT"
        AccessibilityEvent.TYPE_SPEECH_STATE_CHANGE -> "SPEECH_STATE_CHANGE"
        else -> "UNKNOWN_$type"
    }

    // ---- Per-action capture mode (CapturedAction / ActionCaptureRepository) ----

    private fun handlePerActionCapture(event: AccessibilityEvent, sourceNode: AccessibilityNodeInfo?) {
        if (!actionCaptureSessionActive) {
            actionCaptureSessionActive = true
            pendingTextField = null
            lastScrollPosition.clear()
            currentActiveApp = null
        }

        val packageName = event.packageName?.toString() ?: ""
        // Captured now, before the caller recycles sourceNode further up the call stack.
        val fingerprint = sourceNode?.let { buildFingerprint(it) }
        val nodeIdentityKey: Int? = sourceNode?.hashCode()

        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_CLICKED -> handleTapAction(fingerprint, packageName)
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED ->
                handleTypeChangedAction(event, fingerprint, nodeIdentityKey, packageName)
            AccessibilityEvent.TYPE_VIEW_FOCUSED ->
                handleFocusChangedForTextCommit(fingerprint, nodeIdentityKey)
            AccessibilityEvent.TYPE_VIEW_SCROLLED ->
                handleScrollAction(event, fingerprint, nodeIdentityKey, packageName)
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> handleScreenTransition(packageName)
            // TYPE_WINDOW_CONTENT_CHANGED and everything else is confirmed animation/re-render
            // noise unconnected to a real user action in this mode — never turned into a
            // CapturedAction, not even a discarded/logged one.
            else -> {}
        }
    }

    private fun handleTapAction(fingerprint: AccessibilityFingerprint?, packageName: String) {
        val actionIndex = ActionCaptureRepository.nextActionIndex()
        ActionCaptureRepository.addAction(
            CapturedAction(
                actionIndex = actionIndex,
                actionType = "tap",
                timestampMs = System.currentTimeMillis(),
                packageName = packageName,
                accessibilityFingerprint = fingerprint
            )
        )

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            captureActionScreenshot(actionIndex)
        } else {
            // No takeScreenshot() below API 30 — flagged explicitly rather than left looking like
            // an unexplained missing capture.
            ActionCaptureRepository.updateAction(actionIndex) { it.copy(screenshotBlocked = true) }
        }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun captureActionScreenshot(actionIndex: Int) {
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            screenshotExecutor,
            object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    val bitmap = decodeScreenshot(actionIndex, screenshot)
                    if (bitmap == null) {
                        ActionCaptureRepository.updateAction(actionIndex) {
                            it.copy(screenshotBlocked = true, screenshotPath = null)
                        }
                        return
                    }

                    // A takeScreenshot() "success" that returns a blank/solid frame is almost
                    // always FLAG_SECURE too — surfaced the same way as an explicit failure rather
                    // than silently storing a useless image.
                    if (ScreenshotStore.looksBlank(bitmap)) {
                        bitmap.recycle()
                        ActionCaptureRepository.updateAction(actionIndex) {
                            it.copy(screenshotBlocked = true, screenshotPath = null)
                        }
                        return
                    }

                    val path = ScreenshotStore.save(applicationContext, actionIndex, bitmap, ACTION_SCREENSHOT_DIR_NAME)
                    bitmap.recycle()
                    ActionCaptureRepository.updateAction(actionIndex) {
                        it.copy(screenshotPath = path, screenshotBlocked = path == null)
                    }
                }

                override fun onFailure(errorCode: Int) {
                    // ERROR_TAKE_SCREENSHOT_SECURE_WINDOW is the real FLAG_SECURE signal; other
                    // codes (e.g. rate-limiting from two taps in quick succession) aren't, but
                    // this mode has no separate reason field, so all failures collapse to blocked.
                    Log.w(TAG, "Action-capture takeScreenshot failed for action $actionIndex: errorCode=$errorCode")
                    ActionCaptureRepository.updateAction(actionIndex) {
                        it.copy(screenshotBlocked = true, screenshotPath = null)
                    }
                }
            }
        )
    }

    private fun handleTypeChangedAction(
        event: AccessibilityEvent,
        fingerprint: AccessibilityFingerprint?,
        nodeIdentityKey: Int?,
        packageName: String
    ) {
        val key: Any = fingerprint?.resourceId ?: nodeIdentityKey ?: packageName
        // TYPE_VIEW_TEXT_CHANGED's event.text carries the field's current full text (not a
        // keystroke diff), so each new event simply overwrites the buffered value.
        val latestText = event.text?.filterNotNull()?.joinToString(separator = "") { it.toString() } ?: ""

        val current = pendingTextField
        if (current != null && current.key != key) {
            commitPendingTextField()
        }
        pendingTextField = PendingTextField(key, fingerprint, packageName, latestText)
    }

    private fun handleFocusChangedForTextCommit(fingerprint: AccessibilityFingerprint?, nodeIdentityKey: Int?) {
        val current = pendingTextField ?: return
        val newFocusKey: Any = fingerprint?.resourceId ?: nodeIdentityKey ?: return
        if (newFocusKey != current.key) {
            commitPendingTextField()
        }
    }

    private fun commitPendingTextField() {
        val field = pendingTextField ?: return
        pendingTextField = null
        ActionCaptureRepository.addAction(
            CapturedAction(
                actionIndex = ActionCaptureRepository.nextActionIndex(),
                actionType = "type",
                timestampMs = System.currentTimeMillis(),
                packageName = field.packageName,
                accessibilityFingerprint = field.fingerprint,
                committedText = field.latestText
            )
        )
    }

    private fun handleScrollAction(
        event: AccessibilityEvent,
        fingerprint: AccessibilityFingerprint?,
        nodeIdentityKey: Int?,
        packageName: String
    ) {
        val key: Any = fingerprint?.resourceId ?: nodeIdentityKey ?: packageName
        val direction = inferScrollDirection(key, event.scrollY)

        ActionCaptureRepository.addAction(
            CapturedAction(
                actionIndex = ActionCaptureRepository.nextActionIndex(),
                actionType = "scroll",
                timestampMs = System.currentTimeMillis(),
                packageName = packageName,
                accessibilityFingerprint = fingerprint,
                scrollDirection = direction,
                scrollDeltaUnreliable = true
            )
        )
    }

    /**
     * event.scrollY was confirmed to always read 0 in testing even during real scrolling, so in
     * practice this almost always resolves to "unknown" — that's the honest result, not a bug.
     * Kept as a best-effort comparison in case a future device/app actually reports real values.
     */
    private fun inferScrollDirection(key: Any, y: Int): String {
        val previousY = lastScrollPosition[key]
        lastScrollPosition[key] = y
        return when {
            previousY == null -> "unknown"
            y > previousY -> "down"
            y < previousY -> "up"
            else -> "unknown"
        }
    }

    private fun handleScreenTransition(packageName: String) {
        // Always logged, even for IME/systemUI/blank packages — see the active-app-tracking
        // filter below, which only gates whether this flips currentActiveApp.
        ActionCaptureRepository.addAction(
            CapturedAction(
                actionIndex = ActionCaptureRepository.nextActionIndex(),
                actionType = "screen_transition",
                timestampMs = System.currentTimeMillis(),
                packageName = packageName
            )
        )

        if (isIgnoredForActiveAppTracking(packageName)) return
        if (packageName != currentActiveApp) {
            currentActiveApp = packageName
            // A genuine navigation away is the closest available proxy for "an IME done/search/
            // next action fired": AccessibilityService has no direct callback for IME action
            // buttons, only their usual downstream effect of the keyboard closing and the screen
            // changing.
            commitPendingTextField()
        }
    }

    private fun isIgnoredForActiveAppTracking(packageName: String): Boolean {
        if (packageName.isBlank()) return true
        if (packageName == SYSTEM_UI_PACKAGE) return true
        val currentImePackage = try {
            Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD)
                ?.substringBefore('/')
        } catch (e: Exception) {
            null
        }
        return packageName == currentImePackage
    }

    private fun buildFingerprint(node: AccessibilityNodeInfo): AccessibilityFingerprint {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        return AccessibilityFingerprint(
            resourceId = node.viewIdResourceName,
            className = node.className?.toString(),
            text = node.text?.toString(),
            contentDescription = node.contentDescription?.toString(),
            bounds = Bounds(rect.left, rect.top, rect.right, rect.bottom)
        )
    }

    /** Small ring buffer of recent raw touch points, used to correlate with click events by timestamp. */
    private class TouchPointBuffer {
        private val points = ArrayDeque<TouchPoint>()
        private val lock = Any()

        fun record(point: TouchPoint) {
            synchronized(lock) {
                points.addLast(point)
                while (points.size > TOUCH_BUFFER_MAX_SIZE) {
                    points.removeFirst()
                }
            }
        }

        fun findNearest(timestampMs: Long, windowMs: Long): TouchPoint? {
            synchronized(lock) {
                return points
                    .filter { abs(it.timestampMs - timestampMs) <= windowMs }
                    .minByOrNull { abs(it.timestampMs - timestampMs) }
            }
        }
    }
}
