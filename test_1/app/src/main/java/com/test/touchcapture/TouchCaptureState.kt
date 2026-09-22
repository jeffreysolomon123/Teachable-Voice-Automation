package com.test.touchcapture

import kotlinx.coroutines.flow.MutableStateFlow

data class TouchRecord(
    val action: String,
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
    val totalCount = MutableStateFlow(0)

    private const val MAX_DISPLAYED = 20

    @Synchronized
    fun recordTouch(record: TouchRecord) {
        totalCount.value = totalCount.value + 1
        touches.value = (listOf(record) + touches.value).take(MAX_DISPLAYED)
    }

    fun resetCounts() {
        totalCount.value = 0
        touches.value = emptyList()
    }
}
