package com.uirecorder.app.ocr

import android.graphics.Bitmap
import android.graphics.Rect
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.uirecorder.app.data.Bounds
import com.uirecorder.app.data.OcrCandidate
import com.uirecorder.app.data.TouchPoint
import kotlin.math.hypot
import kotlin.math.min
import kotlinx.coroutines.tasks.await

data class OcrResult(
    val candidates: List<OcrCandidate>,
    val methodUsed: String
)

/**
 * Runs ML Kit's on-device text recognizer against tap-capture screenshots, and tests whether it
 * has the same blind spot observed with Tesseract: solid-color CTA buttons (e.g. "Add to cart")
 * being skipped when the whole screen is analyzed in one pass.
 *
 * Strategy: OCR the whole image first. We don't know a priori which button was the "important"
 * one, so the practical stand-in is whether any candidate lands near the recorded touch point —
 * that's the same location a human reviewer will be judging against in the review screen. If
 * nothing lands nearby, fall back to slicing the image into overlapping horizontal bands and
 * OCR'ing each independently, then use whichever approach actually found something near the tap.
 */
class OcrProcessor {

    companion object {
        private const val NEAR_TOUCH_THRESHOLD_PX = 180f
        private const val TILE_COUNT = 4
        private const val TILE_OVERLAP_PX = 120
    }

    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    suspend fun process(bitmap: Bitmap, touchPoint: TouchPoint?): OcrResult {
        val wholeImage = recognizeCandidates(bitmap, touchPoint, offsetY = 0)
        val wholeImageHitNearTouch = touchPoint != null && wholeImage.hasHitNearTouch()

        if (wholeImage.isNotEmpty() && (touchPoint == null || wholeImageHitNearTouch)) {
            return OcrResult(wholeImage, "whole_image")
        }

        val tiled = recognizeTiled(bitmap, touchPoint)
        val tiledHitNearTouch = touchPoint != null && tiled.hasHitNearTouch()

        return when {
            tiledHitNearTouch -> OcrResult(tiled, "tiled")
            tiled.size > wholeImage.size -> OcrResult(tiled, "tiled")
            else -> OcrResult(wholeImage, "whole_image")
        }
    }

    private fun List<OcrCandidate>.hasHitNearTouch(): Boolean =
        any { (it.distanceFromTouchPoint ?: Float.MAX_VALUE) <= NEAR_TOUCH_THRESHOLD_PX }

    private suspend fun recognizeCandidates(
        bitmap: Bitmap,
        touchPoint: TouchPoint?,
        offsetY: Int
    ): List<OcrCandidate> {
        val input = InputImage.fromBitmap(bitmap, 0)
        val result: Text = recognizer.process(input).await()
        val candidates = mutableListOf<OcrCandidate>()
        for (block in result.textBlocks) {
            for (line in block.lines) {
                val box = line.boundingBox ?: continue
                val shifted = Rect(box.left, box.top + offsetY, box.right, box.bottom + offsetY)
                candidates.add(toCandidate(line.text, shifted, touchPoint))
            }
        }
        return candidates
    }

    private suspend fun recognizeTiled(bitmap: Bitmap, touchPoint: TouchPoint?): List<OcrCandidate> {
        val width = bitmap.width
        val height = bitmap.height
        val tileHeight = (height / TILE_COUNT) + TILE_OVERLAP_PX

        val results = mutableListOf<OcrCandidate>()
        val seenKeys = mutableSetOf<String>()

        var y = 0
        while (y < height) {
            val bottom = min(y + tileHeight, height)
            val tile = Bitmap.createBitmap(bitmap, 0, y, width, bottom - y)
            val tileCandidates = try {
                recognizeCandidates(tile, touchPoint, offsetY = y)
            } finally {
                tile.recycle()
            }
            for (candidate in tileCandidates) {
                val key = "${candidate.text}|${candidate.bounds.left}|${candidate.bounds.top}"
                if (seenKeys.add(key)) results.add(candidate)
            }
            if (bottom >= height) break
            y += (tileHeight - TILE_OVERLAP_PX).coerceAtLeast(1)
        }
        return results
    }

    private fun toCandidate(text: String, rect: Rect, touchPoint: TouchPoint?): OcrCandidate {
        val bounds = Bounds(rect.left, rect.top, rect.right, rect.bottom)
        val distance = touchPoint?.let {
            val cx = (rect.left + rect.right) / 2f
            val cy = (rect.top + rect.bottom) / 2f
            hypot((cx - it.x).toDouble(), (cy - it.y).toDouble()).toFloat()
        }
        return OcrCandidate(text = text, bounds = bounds, distanceFromTouchPoint = distance)
    }
}
