package com.example.taprecorder

import android.graphics.Rect
import org.json.JSONArray
import org.json.JSONObject

/**
 * In-memory log of accessibility events for the recording in progress.
 *
 * Written by [TapLoggerService] (main thread) and started/finished by [RecorderService]
 * (main thread / worker thread), so every access goes through [lock]. Both services run in the
 * app's process, which is what lets them share this singleton.
 *
 * All times are on the SystemClock.uptimeMillis() clock, the same clock as
 * AccessibilityEvent.getEventTime(), so `videoTimeMs = eventUptimeMs - recordingStartUptimeMs`.
 */
object TapLog {
    const val SCHEMA_VERSION = 1
    /** Safety cap so a very long recording cannot exhaust memory. */
    private const val MAX_EVENTS = 100_000

    private val lock = Any()
    private var session: Session? = null

    /** True while the accessibility service is bound (updated by [TapLoggerService]). */
    @Volatile
    var serviceConnected = false
        private set

    private class Session(val startUptimeMs: Long, val meta: JSONObject) {
        val events = JSONArray()
        val keyboardSnapshots = JSONArray()
        val loggerMarkers = JSONArray()
        var droppedEvents = 0
        var debouncedContentChanges = 0
    }

    /** Starts a new session; [meta] holds video/device/clock info written at the top of the JSON. */
    fun begin(startUptimeMs: Long, meta: JSONObject) {
        synchronized(lock) { session = Session(startUptimeMs, meta) }
    }

    /** Uptime at which the current recording started, or null when nothing is being recorded. */
    fun sessionStartUptime(): Long? = synchronized(lock) { session?.startUptimeMs }

    fun record(event: JSONObject) {
        synchronized(lock) {
            val s = session ?: return
            if (s.events.length() >= MAX_EVENTS) s.droppedEvents++ else s.events.put(event)
        }
    }

    /**
     * Records the tap on our own Stop control, which the accessibility service cannot log in
     * time: the session ends before the click event reaches it. Must be called before [end].
     *
     * [uptimeMs] is when the tap was handled (finger-up, like VIEW_CLICKED); [stopSource] is
     * "button" or "notification"; [bounds] is the tapped control in screen pixels, if known.
     */
    fun recordStopTap(uptimeMs: Long, packageName: String, stopSource: String, bounds: Rect?) {
        synchronized(lock) {
            val s = session ?: return
            val event = JSONObject()
                .put("videoTimeMs", uptimeMs - s.startUptimeMs)
                .put("eventUptimeMs", uptimeMs)
                .put("type", "APP_STOP_TAPPED")
                .put("packageName", packageName)
                .put("stopSource", stopSource)
            if (bounds != null && !bounds.isEmpty) {
                event.put(
                    "source", JSONObject()
                        .put("className", "StopButton")
                        .put("text", "Stop")
                        .put(
                            "bounds", JSONObject().put("left", bounds.left).put("top", bounds.top)
                                .put("right", bounds.right).put("bottom", bounds.bottom),
                        ),
                )
            }
            s.events.put(event)
        }
    }

    /** Counts a WINDOW_CONTENT_CHANGED event dropped as a near-duplicate (see [TapLoggerService]). */
    fun countDebounced() {
        synchronized(lock) { session?.let { it.debouncedContentChanges++ } }
    }

    fun recordKeyboard(snapshot: JSONObject) {
        synchronized(lock) { session?.keyboardSnapshots?.put(snapshot) }
    }

    /** Service lifecycle changes, recorded so gaps in the log can be explained. */
    fun onServiceState(connected: Boolean, what: String, uptimeMs: Long) {
        serviceConnected = connected
        marker(what, uptimeMs)
    }

    fun marker(what: String, uptimeMs: Long) {
        synchronized(lock) {
            val s = session ?: return
            s.loggerMarkers.put(JSONObject().put("videoTimeMs", uptimeMs - s.startUptimeMs).put("event", what))
        }
    }

    /** Ends the session and returns the complete JSON document, or null if none was running. */
    fun end(endUptimeMs: Long): JSONObject? = synchronized(lock) {
        val s = session ?: return null
        session = null
        s.meta.getJSONObject("clock").put("recordingEndUptimeMs", endUptimeMs)
        s.meta.getJSONObject("logger")
            .put("connectedAtEnd", serviceConnected)
            .put("eventCount", s.events.length())
            .put("droppedEvents", s.droppedEvents)
            .put("debouncedContentChanges", s.debouncedContentChanges)
            .put("markers", s.loggerMarkers)
        s.meta.put("events", s.events).put("keyboardSnapshots", s.keyboardSnapshots)
    }

    /** Drops the current session without producing output (recording cancelled or failed). */
    fun discard() {
        synchronized(lock) { session = null }
    }
}
