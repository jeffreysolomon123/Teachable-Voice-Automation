package com.test.markeronss

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Ticks once per second on the main thread. The accessibility service and the UI share that
 * thread, so if touch handling ever stalls it, the counter freezes and [worstStallMs] records
 * how late the next tick was.
 */
object Heartbeat {
    private const val PERIOD_MS = 1000L

    val count = MutableStateFlow(0L)
    val worstStallMs = MutableStateFlow(0L)

    private val handler = Handler(Looper.getMainLooper())
    private var lastTick = 0L
    private var started = false

    private val tick = object : Runnable {
        override fun run() {
            val now = SystemClock.elapsedRealtime()
            val stall = now - lastTick - PERIOD_MS
            lastTick = now
            count.value += 1
            if (stall > worstStallMs.value) worstStallMs.value = stall
            handler.postDelayed(this, PERIOD_MS)
        }
    }

    /** Main thread only. Idempotent. */
    fun start() {
        if (started) return
        started = true
        lastTick = SystemClock.elapsedRealtime()
        handler.postDelayed(tick, PERIOD_MS)
    }

    fun resetWorstStall() {
        worstStallMs.value = 0
    }
}
