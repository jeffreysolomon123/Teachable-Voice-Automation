package com.example.flowlaunchertest

import kotlinx.coroutines.flow.MutableStateFlow

sealed class OverlayState {
    data object Hidden : OverlayState()
    data class Status(val text: String) : OverlayState()
    /** ASK_USER from the backend: show the reason with Confirm / Stop buttons. */
    data class Asking(val reason: String) : OverlayState()
    /** TEACH recording in progress: red pill with elapsed time and a Stop button. */
    data class Recording(val elapsedSeconds: Long) : OverlayState()
    /** Demo: sticky card shown while the user is in another app after an automation starts. */
    data object Agent : OverlayState()
    data object Ready : OverlayState()
    data class Blocked(val reason: String) : OverlayState()
}

/** In-process channel between MainActivity, ReplayController and OverlayService. */
object OverlayBus {
    val state = MutableStateFlow<OverlayState>(OverlayState.Hidden)

    /**
     * True while a gesture is injected or a screenshot is taken: the overlay hides itself and stops
     * accepting touches so it is neither captured nor hit by the injected tap.
     */
    val hideForInput = MutableStateFlow(false)
}
