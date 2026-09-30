package com.example.flowlaunchertest

import android.util.Log
import kotlinx.coroutines.delay
import org.json.JSONObject

/**
 * Executes one backend action. No UI decisions happen here: the backend already chose the element
 * and computed coordinates from the screenshot; this class only scales them to display pixels and
 * performs the gesture. Returns the result string the backend expects in /replay/step.
 */
class ActionExecutor(private val service: WindowWatcherAccessibilityService) {

    companion object {
        private const val TAG = "ActionExecutor"
        const val OK = "ok"
        const val INPUT_NOT_SUPPORTED = "input_not_supported"
        const val GESTURE_FAILED = "gesture_failed"
        const val APP_NOT_INSTALLED = "app_not_installed"
    }

    /**
     * @param displayWidth/displayHeight size of the full-resolution screenshot (display pixels).
     */
    suspend fun execute(action: JSONObject, displayWidthIn: Int, displayHeightIn: Int): String {
        val type = action.getString("action")
        // Coordinates refer to the uploaded (possibly downscaled) screenshot of screen_width x screen_height.
        val sw = action.optInt("screen_width", 0).takeIf { it > 0 } ?: displayWidthIn.coerceAtLeast(1)
        val sh = action.optInt("screen_height", 0).takeIf { it > 0 } ?: displayHeightIn.coerceAtLeast(1)
        val displayWidth = if (displayWidthIn > 0) displayWidthIn else sw
        val displayHeight = if (displayHeightIn > 0) displayHeightIn else sh
        fun sx(key: String) = (action.getInt(key) * displayWidth / sw.toFloat()).coerceIn(0f, displayWidth - 1f)
        fun sy(key: String) = (action.getInt(key) * displayHeight / sh.toFloat()).coerceIn(0f, displayHeight - 1f)

        return when (type) {
            "LAUNCH_APP" -> {
                val pkg = action.optString("package").ifBlank { null }
                    ?: TARGET_APPS.firstOrNull { it.name.equals(action.optString("app"), true) }?.pkg
                    ?: return APP_NOT_INSTALLED
                val settle = action.optLong("settle_ms", 3000)
                if (service.launchAndSettle(pkg, settle)) OK else APP_NOT_INSTALLED
            }
            "TAP" -> {
                val x = sx("x"); val y = sy("y")
                Log.i(TAG, "TAP ($x, $y) [${action.optString("method")}: ${action.optString("reason")}]")
                if (service.tap(x, y)) OK else GESTURE_FAILED
            }
            "SWIPE" -> {
                val ok = service.swipe(sx("x1"), sy("y1"), sx("x2"), sy("y2"), action.optLong("duration_ms", 400))
                if (ok) OK else GESTURE_FAILED
            }
            "TYPE" -> when (service.typeText(action.getString("text"), action.optBoolean("submit"))) {
                WindowWatcherAccessibilityService.TypeResult.OK -> OK
                // The backend treats this like a failed action: it re-checks the screen / asks the user.
                WindowWatcherAccessibilityService.TypeResult.NO_FOCUSED_FIELD -> GESTURE_FAILED
                WindowWatcherAccessibilityService.TypeResult.UNSUPPORTED -> INPUT_NOT_SUPPORTED
            }
            "BACK" -> if (service.back()) OK else GESTURE_FAILED
            "WAIT" -> {
                delay(action.optLong("duration_ms", 0))
                OK
            }
            else -> {
                Log.w(TAG, "Not an executable action: $type")
                OK
            }
        }
    }
}
