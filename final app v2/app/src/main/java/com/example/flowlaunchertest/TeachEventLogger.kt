package com.example.flowlaunchertest

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Logs UI events into [TapLog] while a TEACH recording is running (ported from TapScreenRecorder's
 * TapLoggerService, whose log format the tap-to-flow pipeline is built and tested against).
 *
 * Only active during a recording ([TapLog.sessionStartUptime] non-null); never records text from
 * password fields and never acts on the UI. Replay does not use any of this: it works from
 * screenshots only. The log tells the video pipeline when and where to look for each tap.
 */
class TeachEventLogger(private val service: AccessibilityService) {

    private var lastKeyboardSignature: Int? = null
    private var lastKeyboardSnapshotUptime = 0L
    // WINDOW_CONTENT_CHANGED is ~80% of all events and never marks a tap: keep one per burst.
    private var lastContentChangeKey: Int? = null
    private var lastContentChangeUptime = 0L

    fun onEvent(event: AccessibilityEvent) {
        val start = TapLog.sessionStartUptime() ?: return
        val type = event.eventType
        if (type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED && isRepeatedContentChange(event)) {
            TapLog.countDebounced()
            return
        }
        val source = event.source
        val password = event.isPassword || source?.isPassword == true

        val obj = JSONObject()
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
                // The source can be a non-clickable child (a label); the clickable ancestor is what was hit.
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

    private fun isRepeatedContentChange(event: AccessibilityEvent): Boolean {
        val key = listOf(event.packageName?.toString(), event.windowId, event.contentChangeTypes).hashCode()
        val repeated = key == lastContentChangeKey && event.eventTime - lastContentChangeUptime < CONTENT_CHANGE_DEBOUNCE_MS
        if (!repeated) {
            lastContentChangeKey = key
            lastContentChangeUptime = event.eventTime
        }
        return repeated
    }

    /**
     * Keyboard window bounds and, when exposed, its keys with bounds. Key presses produce no
     * events; a TEXT_CHANGED event plus the key layout gives the pipeline the tap location.
     */
    private fun snapshotKeyboard(start: Long) {
        val now = SystemClock.uptimeMillis()
        val ime = try {
            service.windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        } catch (_: Exception) {
            null
        }
        val bounds = Rect().also { ime?.getBoundsInScreen(it) }
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
    }
}
