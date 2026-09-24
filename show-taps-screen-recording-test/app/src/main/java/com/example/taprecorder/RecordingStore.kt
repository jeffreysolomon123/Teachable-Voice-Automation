package com.example.taprecorder

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Creates / finalizes the MediaStore entry that receives the video.
 * Going through MediaStore (instead of a raw file path) means no storage permission is needed
 * on Android 10+ and the video shows up in the Gallery under Movies/TapScreenRecorder/.
 */
object RecordingStore {
    private const val PREFS = "recording_store"
    private const val KEY_PENDING_URI = "pending_uri"

    class Output(val uri: Uri, val pfd: ParcelFileDescriptor, val name: String)

    /**
     * Saves the tap-log JSON as Download/TapScreenRecorder/<video name>.json (no permission needed
     * on Android 10+). Returns its MediaStore URI and file name.
     */
    fun saveLog(context: Context, videoName: String, json: String): Pair<Uri, String> {
        val name = videoName.removeSuffix(".mp4") + ".json"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, "application/json")
            put(MediaStore.Downloads.RELATIVE_PATH, "Download/TapScreenRecorder")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: error("MediaStore insert failed")
        try {
            resolver.openOutputStream(uri)?.use { it.write(json.toByteArray(Charsets.UTF_8)) }
                ?: error("openOutputStream returned null")
            resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
        return uri to name
    }

    fun create(context: Context): Output {
        val name = "TapRec_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()) + ".mp4"
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, name)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/TapScreenRecorder")
            // IS_PENDING=1 hides the half-written file from the Gallery until we flip it to 0.
            put(MediaStore.Video.Media.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            ?: error("MediaStore insert failed")
        // Remember the entry so a leftover from a killed process can be cleaned up (see discardOrphan).
        prefs(context).edit().putString(KEY_PENDING_URI, uri.toString()).apply()
        val pfd = try {
            // "rw": the MP4 muxer needs to seek back and rewrite the header when recording stops.
            resolver.openFileDescriptor(uri, "rw") ?: error("openFileDescriptor returned null")
        } catch (e: Exception) {
            discard(context, uri)
            throw e
        }
        return Output(uri, pfd, name)
    }

    /** Recording finished OK: make the video visible in the Gallery. */
    fun publish(context: Context, uri: Uri) {
        val values = ContentValues().apply { put(MediaStore.Video.Media.IS_PENDING, 0) }
        context.contentResolver.update(uri, values, null, null)
        prefs(context).edit().remove(KEY_PENDING_URI).apply()
    }

    /** Recording failed or was unusable: remove the entry (callers must have closed the pfd). */
    fun discard(context: Context, uri: Uri) {
        try {
            context.contentResolver.delete(uri, null, null)
        } catch (_: Exception) {
            // Already gone; nothing to clean up.
        }
        prefs(context).edit().remove(KEY_PENDING_URI).apply()
    }

    /**
     * If the process was killed mid-recording, the pending entry holds a truncated, unplayable MP4.
     * Call this when no recording is running to delete it.
     */
    fun discardOrphan(context: Context) {
        val leftover = prefs(context).getString(KEY_PENDING_URI, null) ?: return
        discard(context, Uri.parse(leftover))
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
