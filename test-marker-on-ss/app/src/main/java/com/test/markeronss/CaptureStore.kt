package com.test.markeronss

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** One click's outcome: either a saved marker screenshot or an explicit failure reason. */
data class Capture(
    val timeMs: Long,
    /** Package of the app the click happened in. */
    val pkg: String,
    val fileName: String?,
    val failure: String?,
    /** Click event → screenshot-callback delay. Large values mean the screen may have moved on. */
    val latencyMs: Long,
    val width: Int,
    val height: Int,
    /** Saved but not fully as intended, e.g. why no marker could be placed. */
    val note: String? = null,
) {
    val ok: Boolean get() = failure == null
}

/** PNGs plus a captures.jsonl index (one JSON object per line, oldest first). */
class CaptureStore(context: Context) {
    private val dir = File(context.getExternalFilesDir(null) ?: context.filesDir, "marker_shots").apply { mkdirs() }
    private val index = File(dir, "captures.jsonl")

    fun file(name: String) = File(dir, name)

    fun newFileName(timeMs: Long): String {
        val stamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date(timeMs))
        return "click_$stamp.png"
    }

    fun append(c: Capture) = synchronized(LOCK) {
        val json = JSONObject()
            .put("t", c.timeMs).put("pkg", c.pkg)
            .put("file", c.fileName ?: JSONObject.NULL)
            .put("failure", c.failure ?: JSONObject.NULL)
            .put("latency", c.latencyMs).put("w", c.width).put("h", c.height)
            .put("note", c.note ?: JSONObject.NULL)
        index.appendText(json.toString() + "\n")
    }

    /** Newest first. */
    fun load(): List<Capture> = synchronized(LOCK) {
        if (!index.exists()) return emptyList()
        index.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                Capture(
                    timeMs = o.getLong("t"), pkg = o.getString("pkg"),
                    fileName = if (o.isNull("file")) null else o.getString("file"),
                    failure = if (o.isNull("failure")) null else o.getString("failure"),
                    latencyMs = o.getLong("latency"), width = o.getInt("w"), height = o.getInt("h"),
                    note = if (o.isNull("note")) null else o.optString("note"),
                )
            }.getOrNull()
        }.asReversed()
    }

    fun clear() = synchronized(LOCK) {
        dir.listFiles()?.forEach { it.delete() }
    }

    private companion object {
        val LOCK = Any()
    }
}
