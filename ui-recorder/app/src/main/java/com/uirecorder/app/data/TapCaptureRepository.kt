package com.uirecorder.app.data

import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Holds the per-tap capture records (screenshot + touch point + OCR) produced by
 * [com.uirecorder.app.service.UiRecorderAccessibilityService] during a recording session, and
 * the manual review judgments collected in the review screen. Screenshot capture and OCR both
 * complete asynchronously well after the triggering tap event, so steps are added immediately
 * with partial data and then patched in place via [updateStep] as results arrive.
 */
object TapCaptureRepository {

    private val stepCounter = AtomicInteger(0)

    private val _tapSteps = MutableStateFlow<List<TapStepRecord>>(emptyList())
    val tapSteps: StateFlow<List<TapStepRecord>> = _tapSteps.asStateFlow()

    fun reset() {
        stepCounter.set(0)
        _tapSteps.value = emptyList()
    }

    fun nextStepIndex(): Int = stepCounter.getAndIncrement()

    fun addStep(record: TapStepRecord) {
        _tapSteps.update { it + record }
    }

    fun updateStep(stepIndex: Int, transform: (TapStepRecord) -> TapStepRecord) {
        _tapSteps.update { list ->
            list.map { if (it.stepIndex == stepIndex) transform(it) else it }
        }
    }

    fun setJudgment(stepIndex: Int, judgment: ReviewJudgment) {
        updateStep(stepIndex) { it.copy(reviewJudgment = judgment) }
    }
}
