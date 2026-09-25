package com.example.taprecorder

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Accessibility service that logs UI events while [RecorderService] is recording.
 *
 * It does nothing outside a recording ([TapLog.sessionStartUptime] is null), never records text
 * from password fields, and never acts on the UI. Configuration: res/xml/tap_logger_config.xml.
 *
 * What it cannot see: raw finger coordinates and taps that produce no accessibility event (most
 * keyboard key presses, many game/WebView/custom views). Those still have to come from the video;
 * this log tells the video pipeline when and where to look, and what was tapped.
 */
class TapLoggerService : AccessibilityService() {

    private var lastKeyboardSignature: Int? = null
    private var lastKeyboardSnapshotUptime = 0L
    // Debounce for WINDOW_CONTENT_CHANGED, which is ~80% of all events and never marks a tap.
    private var lastContentChangeKey: Int? = null
    private var lastContentChangeUptime = 0L

    override fun onServiceConnected() {
        TapLog.onServiceState(true, "connected", SystemClock.uptimeMillis())
    }

    override fun onInterrupt() {
        TapLog.marker("interrupted", SystemClock.uptimeMillis())
    }

    override fun onUnbind(intent: Intent?): Boolean {
        TapLog.onServiceState(false, "unbound", SystemClock.uptimeMillis())
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val start = TapLog.sessionStartUptime() ?: return
        val type = event.eventType
        if (type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED && isRepeatedContentChange(event)) {
            TapLog.countDebounced()
            return
        }
        val source = event.source
        val password = event.isPassword || source?.isPassword == true

        val obj = JSONObject()
            // eventTime is when the event happened, not when it reached us (see receivedUptimeMs).
            .put("videoTimeMs", event.eventTime - start)
            .put("eventUptimeMs", event.eventTime)
            .put("receivedUptimeMs", SystemClock.uptimeMillis())
            .put("type", AccessibilityEvent.eventTypeToString(type).removePrefix("TYPE_"))
            .put("packageName", event.packageName?.toString())
            .put("className", event.className?.toString())
            .put("windowId", event.windowId)
        if (password) {
            obj.put("isPassword", true)
        } else {
            if (event.text.isNotEmpty()) obj.put("text", JSONArray(event.text.map { it?.toString() }))
            obj.put("contentDescription", event.contentDescription?.toString())
        }

        when (type) {
            AccessibilityEvent.TYPE_VIEW_SCROLLED -> obj
                .put("scrollX", event.scrollX).put("scrollY", event.scrollY)
                .put("maxScrollX", event.maxScrollX).put("maxScrollY", event.maxScrollY)
                .put("scrollDeltaX", event.scrollDeltaX).put("scrollDeltaY", event.scrollDeltaY)
                .put("fromIndex", event.fromIndex).put("toIndex", event.toIndex)
                .put("itemCount", event.itemCount)
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> {
                obj.put("fromIndex", event.fromIndex)
                    .put("addedCount", event.addedCount)
                    .put("removedCount", event.removedCount)
                if (!password) obj.put("beforeText", event.beforeText?.toString())
            }
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED -> obj
                .put("fromIndex", event.fromIndex).put("toIndex", event.toIndex)
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED, AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ->
                obj.put("contentChangeTypes", event.contentChangeTypes)
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> obj.put("windowChanges", event.windowChanges)
        }

        if (source != null) {
            obj.put("source", nodeJson(source, password))
            if (type == AccessibilityEvent.TYPE_VIEW_CLICKED || type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
                // The source can be a non-clickable child (e.g. a label); the clickable ancestor's
                // box is what the finger actually hit.
                val target = if (source.isClickable || source.isLongClickable) source else clickableAncestor(source)
                if (target != null && target !== source) obj.put("clickableAncestor", nodeJson(target, password))
                val b = Rect().also { (target ?: source).getBoundsInScreen(it) }
                if (!b.isEmpty) obj.put("approxTap", JSONObject().put("x", b.centerX()).put("y", b.centerY()))
            }
        }
        TapLog.record(obj)

        if (type == AccessibilityEvent.TYPE_WINDOWS_CHANGED || type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            snapshotKeyboard(start)
        }
    }

    /**
     * True if [event] repeats the previous WINDOW_CONTENT_CHANGED (same package, window and
     * change types) within [CONTENT_CHANGE_DEBOUNCE_MS]. Animations and live content (timers,
     * video, loading spinners) fire these continuously; one per burst is enough.
     */
    private fun isRepeatedContentChange(event: AccessibilityEvent): Boolean {
        val key = listOf(event.packageName?.toString(), event.windowId, event.contentChangeTypes).hashCode()
        val repeated = key == lastContentChangeKey &&
            event.eventTime - lastContentChangeUptime < CONTENT_CHANGE_DEBOUNCE_MS
        if (!repeated) {
            lastContentChangeKey = key
            lastContentChangeUptime = event.eventTime
        }
        return repeated
    }

    /**
     * Records the on-screen keyboard's window bounds and, if the keyboard exposes them, its keys
     * with their bounds. Key presses themselves produce no events, but a TEXT_CHANGED event plus
     * the key layout gives the video pipeline an approximate tap location.
     */
    private fun snapshotKeyboard(start: Long) {
        val now = SystemClock.uptimeMillis()
        val ime = try {
            windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        } catch (_: Exception) {
            null
        }
        val bounds = Rect().also { ime?.getBoundsInScreen(it) }
        // Cheap visibility/position check first; walk the key tree at most once a second.
        val quickSignature = if (ime == null) 0 else bounds.hashCode()
        if (quickSignature == lastKeyboardSignature && now - lastKeyboardSnapshotUptime < 1000) return
        lastKeyboardSnapshotUptime = now

        val snapshot = JSONObject().put("videoTimeMs", now - start).put("visible", ime != null)
        if (ime != null) {
            val keys = JSONArray()
            ime.root?.let { collectLabelledLeaves(it, keys, 0) }
            snapshot.put("bounds", rectJson(bounds)).put("keys", keys)
            val signature = 31 * bounds.hashCode() + keys.toString().hashCode()
            if (signature == lastKeyboardSignature) return
            lastKeyboardSignature = signature
        } else {
            if (lastKeyboardSignature == 0) return
            lastKeyboardSignature = 0
        }
        TapLog.recordKeyboard(snapshot)
    }

    private fun collectLabelledLeaves(node: AccessibilityNodeInfo, out: JSONArray, depth: Int) {
        if (out.length() >= MAX_KEYS || depth > 30) return
        val label = node.text?.toString() ?: node.contentDescription?.toString()
        if (node.childCount == 0 && !label.isNullOrEmpty()) {
            out.put(JSONObject().put("label", label).put("bounds", rectJson(Rect().also { node.getBoundsInScreen(it) })))
        }
        for (i in 0 until node.childCount) node.getChild(i)?.let { collectLabelledLeaves(it, out, depth + 1) }
    }

    private fun clickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var current = node.parent
        repeat(6) {
            val n = current ?: return null
            if (n.isClickable || n.isLongClickable) return n
            current = n.parent
        }
        return null
    }

    private fun nodeJson(node: AccessibilityNodeInfo, password: Boolean): JSONObject {
        val obj = JSONObject()
            .put("resourceId", node.viewIdResourceName)
            .put("className", node.className?.toString())
            .put("bounds", rectJson(Rect().also { node.getBoundsInScreen(it) }))
            .put("clickable", node.isClickable)
            .put("longClickable", node.isLongClickable)
            .put("scrollable", node.isScrollable)
            .put("editable", node.isEditable)
            .put("enabled", node.isEnabled)
            .put("visibleToUser", node.isVisibleToUser)
        if (node.isCheckable) obj.put("checked", node.isChecked)
        if (password || node.isPassword) {
            obj.put("isPassword", true)
        } else {
            obj.put("text", node.text?.toString())
                .put("contentDescription", node.contentDescription?.toString())
                .put("hintText", node.hintText?.toString())
        }
        return obj
    }

    private fun rectJson(r: Rect) =
        JSONObject().put("left", r.left).put("top", r.top).put("right", r.right).put("bottom", r.bottom)

    companion object {
        private const val MAX_KEYS = 400
        private const val CONTENT_CHANGE_DEBOUNCE_MS = 250L

        /** Whether the user has switched this service on in Settings > Accessibility. */
        fun isEnabled(context: Context): Boolean {
            val manager = context.getSystemService(AccessibilityManager::class.java) ?: return false
            return manager.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK).any {
                val info = it.resolveInfo.serviceInfo
                info.packageName == context.packageName && info.name == TapLoggerService::class.java.name
            }
        }
    }
}
