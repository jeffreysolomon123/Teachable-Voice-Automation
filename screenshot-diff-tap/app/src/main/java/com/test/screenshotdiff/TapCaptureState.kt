package com.test.screenshotdiff

import android.graphics.Bitmap
import android.graphics.Rect
import kotlinx.coroutines.flow.MutableStateFlow

data class CapturedTap(
    val id: Int,
    val displayBitmap: Bitmap,
    val region: Rect?,
    val confidence: String,
    val timestampMs: Long
)

/** Shared in-process channel between the accessibility service and the Compose UI. */
object TapCaptureState {
    val isWatching = MutableStateFlow(false)
    val heartbeat = MutableStateFlow(0)
    val tapCount = MutableStateFlow(0)
    val capturedTaps = MutableStateFlow<List<CapturedTap>>(emptyList())
}
