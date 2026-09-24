package com.example.taprecorder

import android.net.Uri
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

enum class Phase { Idle, Countdown, Recording, Saving, Saved }

data class RecorderUiState(
    val phase: Phase = Phase.Idle,
    /** Seconds left before recording starts (only meaningful in [Phase.Countdown]). */
    val countdown: Int = 0,
    val elapsedSeconds: Long = 0,
    /** MediaStore URI of the finished video (only in [Phase.Saved]). */
    val savedUri: Uri? = null,
    /** MediaStore URI / file name of the tap-log JSON saved next to the video (only in [Phase.Saved]). */
    val logUri: Uri? = null,
    val logName: String? = null,
    val logEventCount: Int = 0,
    /** One-shot info/error text shown under the button. */
    val message: String? = null,
)

/**
 * Process-wide state shared by the service (writer) and the Compose UI (reader).
 * A plain singleton is enough: the service and the activity live in the same process, and it
 * keeps the UI correct across rotation because it does not belong to the Activity.
 */
object RecorderState {
    private val _state = MutableStateFlow(RecorderUiState())
    val state: StateFlow<RecorderUiState> = _state

    fun update(transform: (RecorderUiState) -> RecorderUiState) = _state.update(transform)
}
