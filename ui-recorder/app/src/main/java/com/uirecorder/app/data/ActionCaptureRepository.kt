package com.uirecorder.app.data

import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * In-process bridge between the per-action-capture path of [com.uirecorder.app.service.UiRecorderAccessibilityService]
 * and the Compose UI. Independent of [RecorderRepository]/[TapCaptureRepository], which back the
 * older whole-event recorder and tap-review flow — this is a separate recording mode with its own
 * Start/Stop lifecycle.
 */
object ActionCaptureRepository {

    private val actionCounter = AtomicInteger(0)

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    private val _actions = MutableStateFlow<List<CapturedAction>>(emptyList())
    val actions: StateFlow<List<CapturedAction>> = _actions.asStateFlow()

    private val _sessionStartMs = MutableStateFlow<Long?>(null)
    val sessionStartMs: StateFlow<Long?> = _sessionStartMs.asStateFlow()

    private val _sessionEndMs = MutableStateFlow<Long?>(null)
    val sessionEndMs: StateFlow<Long?> = _sessionEndMs.asStateFlow()

    fun startRecording() {
        actionCounter.set(0)
        _actions.value = emptyList()
        _sessionEndMs.value = null
        _sessionStartMs.value = System.currentTimeMillis()
        _isRecording.value = true
    }

    fun stopRecording() {
        _isRecording.value = false
        _sessionEndMs.value = System.currentTimeMillis()
    }

    fun nextActionIndex(): Int = actionCounter.getAndIncrement()

    fun addAction(action: CapturedAction) {
        _actions.update { it + action }
    }

    fun updateAction(actionIndex: Int, transform: (CapturedAction) -> CapturedAction) {
        _actions.update { list ->
            list.map { if (it.actionIndex == actionIndex) transform(it) else it }
        }
    }
}
