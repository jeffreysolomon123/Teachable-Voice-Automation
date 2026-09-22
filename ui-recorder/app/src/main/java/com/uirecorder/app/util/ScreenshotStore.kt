package com.uirecorder.app.util

import android.content.Context
import android.graphics.Bitmap
import java.io.File
import java.io.FileOutputStream

/**
 * Saves tap-capture screenshots to local storage and provides a cheap heuristic for detecting
 * the blank/solid-color frames that `takeScreenshot()` can return on FLAG_SECURE screens even
 * when the call itself reports success.
 */
object ScreenshotStore {

    private const val DIR_NAME = "tap_screenshots"
    private const val SAMPLE_GRID = 8
    private const val BLANK_CHANNEL_DELTA_THRESHOLD = 6

    fun save(context: Context, stepIndex: Int, bitmap: Bitmap, dirName: String = DIR_NAME): String? {
        return try {
            val dir = File(context.filesDir, dirName).apply { mkdirs() }
            val file = File(dir, "step_${stepIndex}_${System.currentTimeMillis()}.jpg")
            FileOutputStream(file).use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
            }
            file.absolutePath
        } catch (e: Exception) {
            null
        }
    }

    /** Samples a grid of pixels; if they're all near-identical, the screenshot is likely blank. */
    fun looksBlank(bitmap: Bitmap): Boolean {
        if (bitmap.width < SAMPLE_GRID || bitmap.height < SAMPLE_GRID) return false

        var reference: Int? = null
        var maxDelta = 0
        for (row in 0 until SAMPLE_GRID) {
            for (col in 0 until SAMPLE_GRID) {
                val x = (bitmap.width - 1) * col / (SAMPLE_GRID - 1)
                val y = (bitmap.height - 1) * row / (SAMPLE_GRID - 1)
                val pixel = bitmap.getPixel(x, y)
                val ref = reference
                if (ref == null) {
                    reference = pixel
                } else {
                    val delta = maxChannelDelta(ref, pixel)
                    if (delta > maxDelta) maxDelta = delta
                }
            }
        }
        return maxDelta <= BLANK_CHANNEL_DELTA_THRESHOLD
    }

    private fun maxChannelDelta(a: Int, b: Int): Int {
        val dr = Math.abs(((a shr 16) and 0xFF) - ((b shr 16) and 0xFF))
        val dg = Math.abs(((a shr 8) and 0xFF) - ((b shr 8) and 0xFF))
        val db = Math.abs((a and 0xFF) - (b and 0xFF))
        return maxOf(dr, dg, db)
    }
}
