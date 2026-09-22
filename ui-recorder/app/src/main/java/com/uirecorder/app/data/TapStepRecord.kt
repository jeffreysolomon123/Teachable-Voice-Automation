package com.uirecorder.app.data

import kotlinx.serialization.Serializable

@Serializable
data class TouchPoint(
    val x: Float,
    val y: Float,
    val timestampMs: Long
)

@Serializable
data class OcrCandidate(
    val text: String,
    val bounds: Bounds,
    // Pixel distance from the bounds center to the touch point. Null when there is no touch
    // point to measure against (unsupported API level, or no motion event matched this tap).
    val distanceFromTouchPoint: Float? = null
)

/**
 * Manual per-step verdict filled in during review: "did an OCR candidate near the touch point
 * actually match what was tapped?" This is the experiment this build exists to run.
 */
@Serializable
enum class ReviewJudgment {
    UNREVIEWED, YES, NO, PARTIAL
}

@Serializable
data class TapStepRecord(
    val stepIndex: Int,
    val timestampWallClockMs: Long,
    val packageName: String,
    val eventType: String,

    // Signal 1: accessibility fingerprint (may be entirely null on apps like Amazon/Zomato).
    val accessibilityNode: NodeInfo? = null,
    // The triggering AccessibilityEvent's own announcement text — populated by the app when it
    // dispatches the event, independent of event.source. Can be non-empty even when
    // accessibilityNode is null (observed on Amazon's search bar), so it's worth keeping
    // separately rather than only reading through the node.
    val eventText: List<String> = emptyList(),

    // Signal 2: screenshot, saved to local storage and referenced by path.
    val screenshotPath: String? = null,
    val screenshotBlocked: Boolean = false,
    // Why screenshotBlocked is true: "secure_window" (FLAG_SECURE, a real signal), "blank_pixels"
    // (takeScreenshot reported success but returned a blank/solid frame — also usually
    // FLAG_SECURE), "rate_limited" (called too soon after a previous screenshot — NOT a security
    // signal, just two click events firing close together), or another raw error code name.
    val screenshotBlockedReason: String? = null,

    // Signal 3: raw touch coordinate, independent of the accessibility tree.
    val touchPoint: TouchPoint? = null,
    val touchCaptureSupported: Boolean = false,

    // Signal 4: OCR results against the screenshot.
    val ocrCandidates: List<OcrCandidate> = emptyList(),
    val ocrMethodUsed: String? = null, // "whole_image" or "tiled"; null until OCR finishes
    val ocrProcessed: Boolean = false,

    val reviewJudgment: ReviewJudgment = ReviewJudgment.UNREVIEWED
)
