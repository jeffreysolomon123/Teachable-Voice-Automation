package com.test.touchcapture2

import kotlinx.coroutines.flow.MutableStateFlow

data class TouchRecord(
    val x: Float,
    val y: Float,
    val timestampMs: Long
)

/**
 * Shared in-memory state bridging the AccessibilityService (which captures touches)
 * and the Compose UI (which displays them). The service and the activity run in the
 * same process, so a plain singleton with StateFlow is enough here.
 */
object TouchCaptureState {
    val serviceConnected = MutableStateFlow(false)
    val isWatching = MutableStateFlow(false)
    val watchRequested = MutableStateFlow(false)

    val touches = MutableStateFlow<List<TouchRecord>>(emptyList())
    val totalDownCount = MutableStateFlow(0)

    // Temporary diagnostic signal: confirms ACTION_MOVE events reach the service but
    // are never processed beyond a counter increment. Remove this field and its UI
    // display once that has been confirmed during on-device testing (see build notes).
    val moveEventsIgnoredCount = MutableStateFlow(0L)

    val rateLimitTriggeredCount = MutableStateFlow(0)
    val rateLimitCooldownActive = MutableStateFlow(false)

    private const val MAX_DISPLAYED = 20

    @Synchronized
    fun applyBatch(
        newTouches: List<TouchRecord>,
        moveIgnored: Long,
        rateLimitTriggeredCount: Int,
        cooldownActive: Boolean
    ) {
        if (newTouches.isNotEmpty()) {
            totalDownCount.value = totalDownCount.value + newTouches.size
            touches.value = (newTouches.reversed() + touches.value).take(MAX_DISPLAYED)
        }
        moveEventsIgnoredCount.value = moveIgnored
        this.rateLimitTriggeredCount.value = rateLimitTriggeredCount
        rateLimitCooldownActive.value = cooldownActive
    }

    fun resetCounts() {
        totalDownCount.value = 0
        touches.value = emptyList()
        moveEventsIgnoredCount.value = 0
        rateLimitTriggeredCount.value = 0
        rateLimitCooldownActive.value = false
    }
}
