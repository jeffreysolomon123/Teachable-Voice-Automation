package com.example.flowlaunchertest

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Foreground service hosting the floating status pill (replay progress and ASK_USER prompts). */
class OverlayService : Service() {

    companion object {
        private const val TAG = "OverlayService"
        private const val CHANNEL_ID = "overlay_status"
        private const val NOTIFICATION_ID = 1
        private const val DONE_AUTO_HIDE_MS = 5000L

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, OverlayService::class.java))
        }
    }

    private val scope = MainScope()
    private lateinit var windowManager: WindowManager
    private lateinit var pill: LinearLayout
    private lateinit var label: TextView
    private lateinit var buttons: LinearLayout
    private lateinit var close: TextView
    private lateinit var params: WindowManager.LayoutParams
    private var attached = false
    private var autoHideJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startAsForeground()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        buildPill()
        scope.launch { OverlayBus.state.collect { render(it) } }
        scope.launch { OverlayBus.hideForInput.collect { setHiddenForInput(it) } }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!Settings.canDrawOverlays(this)) {
            Log.e(TAG, "Overlay permission not granted; stopping")
            Toast.makeText(this, "Overlay permission missing — cannot show status", Toast.LENGTH_LONG).show()
            stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun startAsForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Overlay status", NotificationManager.IMPORTANCE_MIN).apply {
                setSound(null, null)
                enableVibration(false)
            }
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Visual automation running")
            .setSilent(true)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, type)
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    @SuppressLint("ClickableViewAccessibility")
    private fun buildPill() {
        label = TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            maxLines = 6
            ellipsize = TextUtils.TruncateAt.END
            maxWidth = (resources.displayMetrics.widthPixels * 0.75).toInt()
        }
        close = TextView(this).apply {
            text = "✕"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(dp(12), dp(2), dp(4), dp(2))
            setOnClickListener { dismiss() }
        }
        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(label)
            addView(close)
        }
        buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            visibility = View.GONE
            addView(Button(context).apply {
                text = "Confirm"
                setOnClickListener { ReplayController.answer(true) }
            })
            addView(Button(context).apply {
                text = "Stop"
                setOnClickListener { ReplayController.answer(false) }
            })
        }
        pill = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(10), dp(10), dp(10))
            elevation = dp(6).toFloat()
            addView(top)
            addView(buttons)
        }
        params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = dp(56)
        }

        // Vertical-only drag.
        var downRawY = 0f
        var startY = 0
        pill.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawY = e.rawY
                    startY = params.y
                }
                MotionEvent.ACTION_MOVE -> {
                    params.y = (startY + (e.rawY - downRawY)).toInt().coerceAtLeast(0)
                    if (attached) windowManager.updateViewLayout(pill, params)
                }
            }
            true
        }
    }

    private fun render(state: OverlayState) {
        Log.i(TAG, "Overlay state -> $state")
        autoHideJob?.cancel()
        if (state is OverlayState.Hidden) {
            detach()
            stopSelf()
            return
        }
        val (text, bg, fg) = when (state) {
            is OverlayState.Status -> Triple(state.text, 0xFF3949AB.toInt(), Color.WHITE)
            is OverlayState.Asking -> Triple(state.reason, 0xFFFFB300.toInt(), Color.BLACK)
            OverlayState.Ready -> Triple("Done ✓", 0xFF2E7D32.toInt(), Color.WHITE)
            is OverlayState.Blocked -> Triple("Stopped: ${state.reason}", 0xFFC62828.toInt(), Color.WHITE)
            OverlayState.Hidden -> error("handled above")
        }
        label.text = text
        label.setTextColor(fg)
        close.setTextColor(fg)
        buttons.visibility = if (state is OverlayState.Asking) View.VISIBLE else View.GONE
        pill.background = GradientDrawable().apply {
            cornerRadius = dp(24).toFloat()
            setColor(bg)
        }
        attach()
        if (state is OverlayState.Ready || state is OverlayState.Blocked) {
            autoHideJob = scope.launch {
                delay(DONE_AUTO_HIDE_MS)
                OverlayBus.state.value = OverlayState.Hidden
            }
        }
    }

    private fun setHiddenForInput(hidden: Boolean) {
        pill.visibility = if (hidden) View.INVISIBLE else View.VISIBLE
        params.flags = if (hidden) {
            params.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
        } else {
            params.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE.inv()
        }
        if (attached) runCatching { windowManager.updateViewLayout(pill, params) }
    }

    private fun dismiss() {
        Log.i(TAG, "Overlay dismissed by user; stopping replay")
        ReplayController.stop()
    }

    private fun attach() {
        if (attached) return
        try {
            windowManager.addView(pill, params)
            attached = true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to add overlay view", e)
            Toast.makeText(this, "Could not show overlay: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun detach() {
        if (!attached) return
        runCatching { windowManager.removeView(pill) }
        attached = false
    }

    override fun onDestroy() {
        scope.cancel()
        detach()
        super.onDestroy()
    }
}
