package com.test.touchcapture

import android.accessibilityservice.AccessibilityService
import android.graphics.PixelFormat
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Deliberately does NOT use onMotionEvent()/SOURCE_TOUCHSCREEN/motionEventSources.
 * That API caused a real device hang in prior testing. This service instead adds a
 * tiny TYPE_ACCESSIBILITY_OVERLAY window and reads MotionEvent.ACTION_OUTSIDE from
 * its own onTouchEvent(), which requires no extra permission beyond the accessibility
 * service binding itself.
 */
class TouchCaptureAccessibilityService : AccessibilityService() {

    private var windowManager: WindowManager? = null
    private var overlayView: View? = null
    private var scope: CoroutineScope? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        TouchCaptureState.serviceConnected.value = true

        val job = SupervisorJob()
        scope = CoroutineScope(Dispatchers.Main + job)
        scope?.launch {
            TouchCaptureState.watchRequested.collect { requested ->
                if (requested) addOverlay() else removeOverlay()
            }
        }
    }

    private fun addOverlay() {
        if (overlayView != null) return

        val view = TouchCatcherView(this)
        val params = WindowManager.LayoutParams(
            1,
            1,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH or
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = 0
        params.y = 0

        try {
            windowManager?.addView(view, params)
            overlayView = view
            TouchCaptureState.isWatching.value = true
        } catch (e: Exception) {
            TouchCaptureState.isWatching.value = false
        }
    }

    private fun removeOverlay() {
        val view = overlayView ?: return
        try {
            windowManager?.removeView(view)
        } catch (e: Exception) {
            // view may already be detached; nothing else to do
        }
        overlayView = null
        TouchCaptureState.isWatching.value = false
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Not used by this test; the service only exists to host the overlay window.
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        removeOverlay()
        TouchCaptureState.serviceConnected.value = false
        scope?.cancel()
        scope = null
        return super.onUnbind(intent)
    }

    private inner class TouchCatcherView(context: android.content.Context) : View(context) {
        override fun onTouchEvent(event: MotionEvent): Boolean {
            val actionName = when (event.action) {
                MotionEvent.ACTION_OUTSIDE -> "OUTSIDE"
                MotionEvent.ACTION_DOWN -> "DOWN"
                MotionEvent.ACTION_UP -> "UP"
                MotionEvent.ACTION_MOVE -> "MOVE"
                MotionEvent.ACTION_CANCEL -> "CANCEL"
                else -> "OTHER(${event.action})"
            }
            TouchCaptureState.recordTouch(
                TouchRecord(
                    action = actionName,
                    x = event.rawX,
                    y = event.rawY,
                    timestampMs = System.currentTimeMillis()
                )
            )
            return false
        }
    }
}
