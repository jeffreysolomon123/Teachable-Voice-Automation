package com.example.taprecorder

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // If a previous process was killed mid-recording, its truncated video is still "pending".
        // Only check on a fresh launch (not rotation) and only when nothing is recording.
        if (savedInstanceState == null && RecorderState.state.value.phase == Phase.Idle) {
            RecordingStore.discardOrphan(this)
        }
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) { RecorderScreen() }
            }
        }
    }
}

@Composable
private fun RecorderScreen() {
    val context = LocalContext.current
    // State lives in RecorderState, not in the Activity, so rotation cannot lose it.
    val ui by RecorderState.state.collectAsStateWithLifecycle()

    // Android 14+: the projection token is single-use, so we ask for it again for every recording.
    val captureLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val data = result.data
        if (result.resultCode == Activity.RESULT_OK && data != null) {
            RecorderService.start(context, result.resultCode, data)
        } else {
            RecorderState.update { it.copy(message = "Screen capture permission was denied") }
        }
    }

    fun requestCapture() {
        RecorderState.update { it.copy(message = null) }
        val manager = context.getSystemService(MediaProjectionManager::class.java)
        captureLauncher.launch(manager.createScreenCaptureIntent())
    }

    // Notification permission only affects whether the notification (and its Stop button) is shown;
    // recording still works if the user declines, so continue either way.
    val notificationLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        requestCapture()
    }

    fun onStartClicked() {
        val needsNotificationPermission = Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        if (needsNotificationPermission) {
            notificationLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            requestCapture()
        }
    }

    // Re-checked on every resume, because the user switches the service on in system Settings.
    var loggerEnabled by remember { mutableStateOf(TapLoggerService.isEnabled(context)) }
    LifecycleResumeEffect(Unit) {
        loggerEnabled = TapLoggerService.isEnabled(context)
        onPauseOrDispose {}
    }
    var showLoggerDisclosure by remember { mutableStateOf(false) }
    if (showLoggerDisclosure) {
        TapLoggerDisclosure(
            onAccept = {
                showLoggerDisclosure = false
                openAccessibilitySettings(context)
            },
            onDismiss = { showLoggerDisclosure = false },
        )
    }

    Box(modifier = Modifier.fillMaxSize().safeDrawingPadding(), contentAlignment = Alignment.Center) {
        Column(
            modifier = Modifier.verticalScroll(rememberScrollState()).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = when (ui.phase) {
                    Phase.Idle -> "Idle"
                    Phase.Countdown -> "Starting…"
                    Phase.Recording -> "Recording"
                    Phase.Saving -> "Saving"
                    Phase.Saved -> "Saved"
                },
                style = MaterialTheme.typography.headlineMedium,
            )

            Text(
                text = when (ui.phase) {
                    Phase.Countdown -> ui.countdown.toString()
                    Phase.Recording -> "%02d:%02d".format(ui.elapsedSeconds / 60, ui.elapsedSeconds % 60)
                    else -> " "
                },
                fontSize = 48.sp,
                fontWeight = FontWeight.Bold,
            )

            Spacer(Modifier.height(16.dp))

            val recordingOrStarting = ui.phase == Phase.Countdown || ui.phase == Phase.Recording
            // Screen bounds of the button, so the Stop tap can be logged with where it was.
            val view = LocalView.current
            var buttonBounds by remember { mutableStateOf<android.graphics.Rect?>(null) }
            Button(
                onClick = {
                    if (recordingOrStarting) {
                        RecorderService.stop(context, SystemClock.uptimeMillis(), buttonBounds)
                    } else {
                        onStartClicked()
                    }
                },
                enabled = ui.phase != Phase.Saving,
                shape = CircleShape,
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (recordingOrStarting) Color(0xFF616161) else Color(0xFFE53935),
                    contentColor = Color.White,
                ),
                modifier = Modifier.size(150.dp).onGloballyPositioned { coords ->
                    val b = coords.boundsInWindow()
                    val origin = IntArray(2).also { view.getLocationOnScreen(it) }
                    buttonBounds = android.graphics.Rect(
                        origin[0] + b.left.toInt(), origin[1] + b.top.toInt(),
                        origin[0] + b.right.toInt(), origin[1] + b.bottom.toInt(),
                    )
                },
            ) {
                Text(if (recordingOrStarting) "Stop" else "Start", fontSize = 26.sp)
            }

            if (ui.phase == Phase.Saved && ui.savedUri != null) {
                Spacer(Modifier.height(16.dp))
                OutlinedButton(onClick = { openInGallery(context, ui.savedUri!!) }) {
                    Text("Open in Gallery")
                }
                val logUri = ui.logUri
                if (logUri != null) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = { shareLog(context, logUri) }) {
                        Text("Share JSON")
                    }
                    Text(
                        "${ui.logEventCount} events saved to Download/TapScreenRecorder/${ui.logName}",
                        style = MaterialTheme.typography.bodySmall,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            Spacer(Modifier.height(24.dp))
            TapLoggerStatus(
                enabled = loggerEnabled,
                onEnableClicked = { showLoggerDisclosure = true },
            )

            ui.message?.let {
                Spacer(Modifier.height(16.dp))
                Text(it, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
            }

            Spacer(Modifier.height(24.dp))
            Text(
                "Enable Show taps in Developer options before recording",
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun TapLoggerStatus(enabled: Boolean, onEnableClicked: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            if (enabled) "Tap logger: ON" else "Tap logger: OFF (only the video will be saved)",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
        )
        if (!enabled) {
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = onEnableClicked) { Text("Enable tap logger") }
        }
    }
}

/** Prominent disclosure shown before sending the user to Accessibility settings. */
@Composable
private fun TapLoggerDisclosure(onAccept: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Enable the tap logger?") },
        text = {
            Text(
                "While a recording is running, TapScreenRecorder uses Android's Accessibility API to log " +
                    "which on-screen elements you tap, scroll and type into (including the text you type, " +
                    "but never password fields). Nothing is logged outside a recording, and the log is only " +
                    "saved on this phone as a JSON file next to the video.\n\n" +
                    "On the next screen, open \"${stringResource(R.string.tap_logger_label)}\" and turn it on. " +
                    "If the switch is greyed out, go to App info > ⋮ > Allow restricted settings first.",
            )
        },
        confirmButton = { TextButton(onClick = onAccept) { Text("Open settings") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Not now") } },
    )
}

private fun openAccessibilitySettings(context: android.content.Context) {
    try {
        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    } catch (_: ActivityNotFoundException) {
        RecorderState.update { it.copy(message = "Could not open Accessibility settings") }
    }
}

private fun shareLog(context: android.content.Context, uri: Uri) {
    val send = Intent(Intent.ACTION_SEND)
        .setType("application/json")
        .putExtra(Intent.EXTRA_STREAM, uri)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    try {
        context.startActivity(Intent.createChooser(send, "Export tap log"))
    } catch (_: ActivityNotFoundException) {
        RecorderState.update { it.copy(message = "No app found to share the JSON") }
    }
}

private fun openInGallery(context: android.content.Context, uri: Uri) {
    val intent = Intent(Intent.ACTION_VIEW)
        .setDataAndType(uri, "video/mp4")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    try {
        context.startActivity(intent)
    } catch (_: ActivityNotFoundException) {
        RecorderState.update { it.copy(message = "No app found to open the video") }
    }
}
