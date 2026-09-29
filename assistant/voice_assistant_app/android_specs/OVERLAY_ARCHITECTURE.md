# Android Siri-Style Edge Glow Floating Overlay Architecture

This document describes how to implement the identical **Siri-style iridescent perimeter edge glow** and persistent voice assistant overlay directly in the Android APK using Kotlin & Jetpack Compose.

---

## 1. Required Permissions (`AndroidManifest.xml`)

To draw over other apps (like Zomato, Domino's, Amazon) and capture voice:

```xml
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE" />
```

---

## 2. Floating Window Service Architecture

Create an Android `ForegroundService` that creates a `ComposeView` and attaches it via `WindowManager`:

```kotlin
class VoiceAssistantOverlayService : Service() {

    private lateinit var windowManager: WindowManager
    private var overlayView: ComposeView? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        showSiriOverlay()
    }

    private fun showSiriOverlay() {
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        )

        overlayView = ComposeView(this).apply {
            setViewTreeSavedStateRegistryOwner(...)
            setViewTreeLifecycleOwner(...)
            setContent {
                SiriEdgeGlowOverlay()
            }
        }

        windowManager.addView(overlayView, params)
    }

    override fun onDestroy() {
        super.onDestroy()
        overlayView?.let { windowManager.removeView(it) }
    }
}
```

---

## 3. Jetpack Compose Siri Edge Glow Implementation

The edge glow is drawn using Compose `Canvas` with an animated sweep/radial gradient brush and blur mask:

```kotlin
@Composable
fun SiriEdgeGlowOverlay(
    isListening: Boolean = true,
    voiceAmplitude: Float = 0.5f // 0.0f to 1.0f from AudioRecord
) {
    val infiniteTransition = rememberInfiniteTransition(label = "glow_rotation")
    val angle by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 5000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "angle"
    )

    val glowColors = listOf(
        Color(0xFF00F0FF), // Neon Cyan
        Color(0xFFFF007A), // Magenta
        Color(0xFF7928CA), // Electric Purple
        Color(0xFF0070F3), // Azure Blue
        Color(0xFFFFB800), // Amber
        Color(0xFF00F0FF)  // Loop back
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(Unit) { /* pass through touches */ }
    ) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val strokeWidth = 24.dp.toPx() * (1f + voiceAmplitude * 0.8f)

            // 1. Draw iridescent perimeter border
            drawRect(
                brush = Brush.sweepGradient(
                    colors = glowColors,
                    center = Offset(size.width / 2f, size.height / 2f)
                ),
                style = Stroke(width = strokeWidth)
            )

            // 2. Draw billowed bottom wave
            drawOval(
                brush = Brush.radialGradient(
                    colors = listOf(
                        Color(0x9900F0FF),
                        Color(0x66FF007A),
                        Color.Transparent
                    ),
                    center = Offset(size.width / 2f, size.height),
                    radius = size.width * 0.75f * (0.8f + voiceAmplitude * 0.5f)
                ),
                topLeft = Offset(-size.width * 0.1f, size.height - 180.dp.toPx()),
                size = Size(size.width * 1.2f, 220.dp.toPx())
            )
        }
    }
}
```

---

## 4. Keeping Assistant Alive Throughout Workflow

1. **Foreground Service Notification**:
   - The service posts an ongoing foreground notification (*"Teachable Assistant active — monitoring workflow"*).
   - This prevents Android's LMK (Low Memory Killer) from killing the assistant process while the user or automated steps are executing inside Zomato or Domino's.
2. **Overlay Minimization / Ambient Mode**:
   - When the user starts demonstrating taps or when replay begins, the edge glow transitions from active `LISTENING` mode to subtle `EXECUTING` mode (a delicate, low-opacity ambient bottom bar).
   - If an error, pop-up (T7), stuck state (T10), or credential boundary (T11) is detected, the overlay immediately wakes up, flashes amber/neon, and speaks the prompt to the user.
