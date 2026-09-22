package com.test.touchcapture2

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log
import android.view.InputDevice
import android.view.MotionEvent
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Observes raw touchscreen ACTION_DOWN events via AccessibilityService.onMotionEvent(),
 * added in API 34. Deliberately does NOT use TouchInteractionController: that requires
 * FLAG_REQUEST_TOUCH_EXPLORATION_MODE, the same mode screen readers use where a single
 * tap only explores/announces and a double tap is needed to activate — unacceptable here.
 * Plain onMotionEvent() observes without consuming, so normal single-tap interaction in
 * the app underneath is unaffected.
 *
 * A prior attempt at this processed every motion event (including ACTION_MOVE, which
 * fires up to hundreds of times per second during any drag/scroll) and did non-trivial
 * work on each one, which is believed to have hung a real device. Every safeguard below
 * is aimed directly at that: only ACTION_DOWN is processed, the callback itself does
 * only a few atomic ops plus a non-suspending channel send, the touch-processing work is
 * isolated in its own SupervisorJob-backed scope, UI-visible state is only updated once
 * per second from a batch timer, and a hard rate limit acts as a safety net against any
 * event flood not anticipated above.
 */
class TouchCaptureAccessibilityService : AccessibilityService() {

    private val supervisorJob = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.Default + supervisorJob)

    // Filled only by onMotionEvent (ACTION_DOWN only); drained by the batch timer below.
    // Unlimited capacity is safe here because the rate limiter guarantees the producer
    // side can never realistically flood it.
    private val pendingDowns = Channel<TouchRecord>(Channel.UNLIMITED)

    private val moveEventsIgnored = AtomicLong(0)
    private val downCountInWindow = AtomicInteger(0)
    private val windowStartMs = AtomicLong(0)
    private val cooldownUntilMs = AtomicLong(0)
    private val rateLimitTriggeredTotal = AtomicInteger(0)

    override fun onServiceConnected() {
        super.onServiceConnected()
        TouchCaptureState.serviceConnected.value = true

        scope.launch {
            TouchCaptureState.watchRequested.collect { requested -> setWatching(requested) }
        }

        scope.launch {
            while (isActive) {
                delay(BATCH_INTERVAL_MS)
                drainAndPublish()
            }
        }
    }

    // Only ACTION_DOWN is processed. ACTION_MOVE, ACTION_UP/CANCEL, and multi-touch
    // pointer events (ACTION_POINTER_DOWN/UP) are ignored outright. Work done here for
    // an accepted event is limited to a few atomic ops and a non-suspending channel
    // send — no UI update, recomposition, disk write, or other heavy work.
    override fun onMotionEvent(event: MotionEvent) {
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> {
                moveEventsIgnored.incrementAndGet()
                return
            }
            MotionEvent.ACTION_DOWN -> Unit
            else -> return
        }

        val now = SystemClock.elapsedRealtime()

        val cooldownUntil = cooldownUntilMs.get()
        if (cooldownUntil != 0L) {
            if (now < cooldownUntil) {
                return
            }
            cooldownUntilMs.set(0L)
            windowStartMs.set(now)
            downCountInWindow.set(0)
        }

        if (now - windowStartMs.get() >= RATE_WINDOW_MS) {
            windowStartMs.set(now)
            downCountInWindow.set(1)
        } else if (downCountInWindow.incrementAndGet() > RATE_LIMIT_PER_WINDOW) {
            cooldownUntilMs.set(now + COOLDOWN_MS)
            rateLimitTriggeredTotal.incrementAndGet()
            Log.w(TAG, "ACTION_DOWN rate limit exceeded; cooling down for ${COOLDOWN_MS}ms")
            return
        }

        pendingDowns.trySend(
            TouchRecord(x = event.x, y = event.y, timestampMs = System.currentTimeMillis())
        )
    }

    private fun drainAndPublish() {
        val batch = ArrayList<TouchRecord>()
        while (true) {
            val record = pendingDowns.tryReceive().getOrNull() ?: break
            batch.add(record)
        }
        TouchCaptureState.applyBatch(
            newTouches = batch,
            moveIgnored = moveEventsIgnored.get(),
            rateLimitTriggeredCount = rateLimitTriggeredTotal.get(),
            cooldownActive = cooldownUntilMs.get() != 0L
        )
    }

    private fun setWatching(enabled: Boolean) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // API 34+ only; MainActivity already blocks reaching this on older devices.
            TouchCaptureState.isWatching.value = false
            return
        }
        val info = serviceInfo
        if (info == null) {
            TouchCaptureState.isWatching.value = false
            return
        }
        // Removing the source (0) is how the API documents stopping observation.
        info.motionEventSources = if (enabled) InputDevice.SOURCE_TOUCHSCREEN else 0
        serviceInfo = info
        TouchCaptureState.isWatching.value = enabled

        if (!enabled) {
            windowStartMs.set(0L)
            downCountInWindow.set(0)
            cooldownUntilMs.set(0L)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Not used by this test; the service only exists to host motion-event observation.
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        setWatching(false)
        TouchCaptureState.serviceConnected.value = false
        scope.cancel()
        return super.onUnbind(intent)
    }

    companion object {
        private const val TAG = "TouchCaptureV2"
        private const val BATCH_INTERVAL_MS = 1000L
        private const val RATE_WINDOW_MS = 1000L
        private const val RATE_LIMIT_PER_WINDOW = 50
        private const val COOLDOWN_MS = 5000L
    }
}
