package com.example.flowlaunchertest

import android.graphics.Bitmap
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow

sealed class OverlayState {
    data object Hidden : OverlayState()
    data class Launching(val app: String) : OverlayState()
    data class Waiting(val app: String) : OverlayState()
    data object Checking : OverlayState()
    data object Ready : OverlayState()
    data class Blocked(val reason: String) : OverlayState()
    data class TimedOut(val app: String) : OverlayState()
    data class NotInstalled(val app: String) : OverlayState()
}

/** In-process channel between MainActivity, the accessibility service and OverlayService. */
object OverlayBus {
    val state = MutableStateFlow<OverlayState>(OverlayState.Hidden)

    /** True while a screenshot is being taken, so the overlay hides itself and isn't captured. */
    val capturing = MutableStateFlow(false)

    /** Screenshots waiting for the LLM readiness check (consumed by OverlayService). */
    val screenshots = MutableSharedFlow<Bitmap>(extraBufferCapacity = 1)
}
