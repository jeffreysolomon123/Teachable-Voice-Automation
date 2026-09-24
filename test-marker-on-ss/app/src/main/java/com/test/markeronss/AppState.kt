package com.test.markeronss

import kotlinx.coroutines.flow.MutableStateFlow
import java.util.concurrent.atomic.AtomicInteger

/** Process-wide hand-off between the accessibility service and the UI. */
object AppState {
    val connected = MutableStateFlow(false)
    val watching = MutableStateFlow(false)

    /** Clicks taken off the queue for processing. */
    val clicksCaptured = MutableStateFlow(0)
    val saved = MutableStateFlow(0)
    val failed = MutableStateFlow(0)

    /** Clicks discarded by the queue-full guard. */
    val dropped = MutableStateFlow(0)

    /**
     * Clicks discarded by the spacing gate in onAccessibilityEvent. A plain atomic, not a flow, so
     * the callback does no more than an increment; the UI polls it once per heartbeat tick.
     */
    val throttled = AtomicInteger(0)

    /** Bumped every time a capture record is written, so the gallery reloads. */
    val capturesVersion = MutableStateFlow(0)
    val status = MutableStateFlow("Idle")

    /** Clicks inside this app itself (toggle, gallery) are ignored, not captured. */
    @Volatile
    var appInForeground = false
}
