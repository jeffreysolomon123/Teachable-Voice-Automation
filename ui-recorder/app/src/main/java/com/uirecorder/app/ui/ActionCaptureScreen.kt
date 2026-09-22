package com.uirecorder.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.uirecorder.app.data.ActionCaptureSession
import com.uirecorder.app.util.AccessibilityUtils
import com.uirecorder.app.util.ActionCaptureFormatter
import kotlinx.coroutines.launch

/**
 * Per-Action Capture Recorder: a standalone testing/inspection mode, independent of the
 * whole-event recorder ([RecorderScreen]) and its tap-review flow ([TapReviewScreen]). Record,
 * stop, then read the raw JSON directly — no report or export flow, this view IS the deliverable.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ActionCaptureScreen(viewModel: RecorderViewModel, onBack: () -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    var accessibilityEnabled by remember {
        mutableStateOf(AccessibilityUtils.isAccessibilityServiceEnabled(context))
    }

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                accessibilityEnabled = AccessibilityUtils.isAccessibilityServiceEnabled(context)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val isRecording by viewModel.actionCaptureIsRecording.collectAsStateWithLifecycle()
    val actions by viewModel.capturedActions.collectAsStateWithLifecycle()
    val sessionStartMs by viewModel.actionCaptureSessionStartMs.collectAsStateWithLifecycle()
    val sessionEndMs by viewModel.actionCaptureSessionEndMs.collectAsStateWithLifecycle()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    val hasStoppedSession = !isRecording && sessionStartMs != null && sessionEndMs != null
    val sessionJson = if (hasStoppedSession) {
        ActionCaptureFormatter.toJson(ActionCaptureSession(sessionStartMs!!, sessionEndMs!!, actions))
    } else {
        null
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text("Action Capture Recorder") },
                navigationIcon = {
                    OutlinedButton(onClick = onBack, modifier = Modifier.padding(start = 8.dp)) {
                        Text("Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
        ) {
            AccessibilityStatusRow(
                enabled = accessibilityEnabled,
                onEnableClick = { AccessibilityUtils.openAccessibilitySettings(context) }
            )
            HorizontalDivider()

            Button(
                onClick = { if (isRecording) viewModel.stopActionCapture() else viewModel.startActionCapture() },
                enabled = accessibilityEnabled,
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (isRecording) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                Text(if (isRecording) "Stop Recording" else "Start Recording")
            }

            if (!accessibilityEnabled) {
                Text(
                    "Enable the accessibility service above to start recording.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }

            if (isRecording) {
                Text(
                    "Recording… ${actions.size} action(s) captured. Switch to another app and interact with it.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }

            if (sessionJson != null) {
                SessionJsonView(
                    json = sessionJson,
                    actionCount = actions.size,
                    onCopy = {
                        copyToClipboard(context, "Action Capture JSON", sessionJson)
                        scope.launch { snackbarHostState.showSnackbar("Copied ${actions.size} actions to clipboard") }
                    },
                    modifier = Modifier
                        .fillMaxSize()
                        .weight(1f, fill = true)
                )
            } else if (!isRecording) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        "No session yet. Press Start Recording, then switch to another app.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(32.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun SessionJsonView(json: String, actionCount: Int, onCopy: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier = modifier.padding(horizontal = 16.dp)) {
        Text("Session JSON ($actionCount action(s))", fontWeight = FontWeight.Bold)
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedButton(onClick = onCopy) { Text("Copy to clipboard") }
        Spacer(modifier = Modifier.height(8.dp))
        Box(
            modifier = Modifier
                .fillMaxSize()
                .clip(RoundedCornerShape(8.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant)
        ) {
            Text(
                json,
                fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .verticalScroll(rememberScrollState())
                    .padding(12.dp)
            )
        }
        Spacer(modifier = Modifier.height(16.dp))
    }
}

@Composable
private fun AccessibilityStatusRow(enabled: Boolean, onEnableClick: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        Text(
            if (enabled) "Accessibility service enabled" else "Accessibility service disabled",
            color = if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            fontWeight = FontWeight.Medium
        )
        if (!enabled) {
            OutlinedButton(onClick = onEnableClick) { Text("Enable") }
        }
    }
}

private fun copyToClipboard(context: Context, label: String, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText(label, text))
}
