package com.uirecorder.app.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * In-process bridge between the always-running [com.uirecorder.app.service.UiRecorderAccessibilityService]
 * and the Compose UI. The service writes events here; the UI collects the flow directly.
 */
object RecorderRepository {

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    private val _fullTreeCaptureEnabled = MutableStateFlow(false)
    val fullTreeCaptureEnabled: StateFlow<Boolean> = _fullTreeCaptureEnabled.asStateFlow()

    // Off by default and never enabled just by turning on the accessibility service — raw
    // motion event capture (AccessibilityServiceInfo#setMotionEventSources) has been observed
    // to freeze touch input system-wide on at least one real device (MIUI/HyperOS). Requires an
    // explicit, separate opt-in from the UI, with the risk called out there.
    private val _rawTouchCaptureEnabled = MutableStateFlow(false)
    val rawTouchCaptureEnabled: StateFlow<Boolean> = _rawTouchCaptureEnabled.asStateFlow()

    private val _events = MutableStateFlow<List<UiEvent>>(emptyList())
    val events: StateFlow<List<UiEvent>> = _events.asStateFlow()

    private val _sessionStartMs = MutableStateFlow<Long?>(null)
    val sessionStartMs: StateFlow<Long?> = _sessionStartMs.asStateFlow()

    private val _sessionEndMs = MutableStateFlow<Long?>(null)
    val sessionEndMs: StateFlow<Long?> = _sessionEndMs.asStateFlow()

    fun startRecording() {
        _events.value = emptyList()
        _sessionEndMs.value = null
        _sessionStartMs.value = System.currentTimeMillis()
        _isRecording.value = true
    }

    fun stopRecording() {
        _isRecording.value = false
        _sessionEndMs.value = System.currentTimeMillis()
    }

    fun setFullTreeCaptureEnabled(enabled: Boolean) {
        _fullTreeCaptureEnabled.value = enabled
    }

    fun setRawTouchCaptureEnabled(enabled: Boolean) {
        _rawTouchCaptureEnabled.value = enabled
    }

    fun addEvent(event: UiEvent) {
        _events.update { it + event }
    }
}
