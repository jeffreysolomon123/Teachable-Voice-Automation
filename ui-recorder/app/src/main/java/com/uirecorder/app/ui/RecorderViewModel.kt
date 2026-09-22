package com.uirecorder.app.ui

import androidx.lifecycle.ViewModel
import com.uirecorder.app.data.ActionCaptureRepository
import com.uirecorder.app.data.CapturedAction
import com.uirecorder.app.data.RecorderRepository
import com.uirecorder.app.data.ReviewJudgment
import com.uirecorder.app.data.TapCaptureRepository
import com.uirecorder.app.data.TapStepRecord
import com.uirecorder.app.data.UiEvent
import kotlinx.coroutines.flow.StateFlow

class RecorderViewModel : ViewModel() {
    val events: StateFlow<List<UiEvent>> = RecorderRepository.events
    val isRecording: StateFlow<Boolean> = RecorderRepository.isRecording
    val fullTreeCaptureEnabled: StateFlow<Boolean> = RecorderRepository.fullTreeCaptureEnabled
    val rawTouchCaptureEnabled: StateFlow<Boolean> = RecorderRepository.rawTouchCaptureEnabled
    val sessionStartMs: StateFlow<Long?> = RecorderRepository.sessionStartMs
    val sessionEndMs: StateFlow<Long?> = RecorderRepository.sessionEndMs

    val tapSteps: StateFlow<List<TapStepRecord>> = TapCaptureRepository.tapSteps

    val actionCaptureIsRecording: StateFlow<Boolean> = ActionCaptureRepository.isRecording
    val capturedActions: StateFlow<List<CapturedAction>> = ActionCaptureRepository.actions
    val actionCaptureSessionStartMs: StateFlow<Long?> = ActionCaptureRepository.sessionStartMs
    val actionCaptureSessionEndMs: StateFlow<Long?> = ActionCaptureRepository.sessionEndMs

    fun startRecording() {
        TapCaptureRepository.reset()
        RecorderRepository.startRecording()
    }

    fun stopRecording() = RecorderRepository.stopRecording()
    fun setFullTreeCaptureEnabled(enabled: Boolean) = RecorderRepository.setFullTreeCaptureEnabled(enabled)
    fun setRawTouchCaptureEnabled(enabled: Boolean) = RecorderRepository.setRawTouchCaptureEnabled(enabled)
    fun setTapJudgment(stepIndex: Int, judgment: ReviewJudgment) = TapCaptureRepository.setJudgment(stepIndex, judgment)

    fun startActionCapture() = ActionCaptureRepository.startRecording()
    fun stopActionCapture() = ActionCaptureRepository.stopRecording()
}
