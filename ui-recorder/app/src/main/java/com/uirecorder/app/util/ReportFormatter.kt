package com.uirecorder.app.util

import com.uirecorder.app.data.RecordingSession
import kotlinx.serialization.json.Json
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object ReportFormatter {

    private val json = Json {
        prettyPrint = true
        encodeDefaults = true
    }

    fun toJson(session: RecordingSession): String =
        json.encodeToString(RecordingSession.serializer(), session)

    fun toPlainText(session: RecordingSession): String {
        val events = session.events
        val durationMs = (session.endedAtMs - session.startedAtMs).coerceAtLeast(0)
        val packages = events.map { it.packageName }.distinct()
        val headerDate = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date(session.startedAtMs))

        val sb = StringBuilder()
        sb.appendLine("UI Action Recording — $headerDate")
        sb.appendLine(
            "Duration: ${formatDuration(durationMs)} | Events: ${events.size} | Apps seen: ${packages.joinToString(", ")}"
        )
        sb.appendLine()

        for (event in events) {
            val relativeMs = (event.timestampWallClockMs - session.startedAtMs).coerceAtLeast(0)
            sb.appendLine("[${formatElapsed(relativeMs)}] ${event.eventType}  ${event.packageName}")

            val source = event.source
            sb.appendLine("  resourceId: ${source?.resourceId.orNone()}")

            val textSummary = (event.eventText.firstOrNull() ?: source?.text)?.let { "\"$it\"" } ?: "(none)"
            val descSummary = source?.contentDescription?.let { "\"$it\"" } ?: "(none)"
            sb.appendLine("  text: $textSummary   contentDescription: $descSummary")

            source?.bounds?.let {
                sb.appendLine("  bounds: (${it.left},${it.top})-(${it.right},${it.bottom})")
            }
            if (source != null) {
                sb.appendLine(
                    "  clickable: ${source.isClickable}  enabled: ${source.isEnabled}  scrollable: ${source.isScrollable}"
                )
            }
            if (event.eventType == "VIEW_SCROLLED") {
                sb.appendLine(
                    "  scrollDelta: (${event.scrollDeltaX ?: 0},${event.scrollDeltaY ?: 0})  scrollPos: (${event.scrollX ?: 0},${event.scrollY ?: 0})"
                )
            }
            if (event.eventType == "VIEW_TEXT_CHANGED") {
                sb.appendLine(
                    "  beforeText: \"${event.beforeText ?: ""}\"  addedCount: ${event.addedCount ?: 0}  removedCount: ${event.removedCount ?: 0}"
                )
            }
            if (!event.fullTreeSnapshot.isNullOrEmpty()) {
                sb.appendLine("  fullTreeSnapshot: ${event.fullTreeSnapshot.size} nodes")
            }
            sb.appendLine()
        }
        return sb.toString()
    }

    private fun String?.orNone(): String = if (this.isNullOrEmpty()) "(none)" else this

    private fun formatDuration(ms: Long): String {
        val totalSeconds = ms / 1000
        val minutes = totalSeconds / 60
        val seconds = totalSeconds % 60
        return String.format(Locale.US, "%02d:%02d", minutes, seconds)
    }

    private fun formatElapsed(ms: Long): String {
        val minutes = ms / 60000
        val seconds = (ms % 60000) / 1000
        val millis = ms % 1000
        return String.format(Locale.US, "%02d:%02d.%03d", minutes, seconds, millis)
    }
}
