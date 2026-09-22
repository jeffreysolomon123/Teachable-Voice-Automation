package com.uirecorder.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.uirecorder.app.data.RecordingSession
import com.uirecorder.app.data.UiEvent
import com.uirecorder.app.util.AccessibilityUtils
import com.uirecorder.app.util.ReportFormatter
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecorderScreen(viewModel: RecorderViewModel, onOpenReview: () -> Unit, onOpenActionCapture: () -> Unit) {
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

    val isRecording by viewModel.isRecording.collectAsStateWithLifecycle()
    val fullTreeCaptureEnabled by viewModel.fullTreeCaptureEnabled.collectAsStateWithLifecycle()
    val rawTouchCaptureEnabled by viewModel.rawTouchCaptureEnabled.collectAsStateWithLifecycle()
    val events by viewModel.events.collectAsStateWithLifecycle()
    val sessionStartMs by viewModel.sessionStartMs.collectAsStateWithLifecycle()
    val sessionEndMs by viewModel.sessionEndMs.collectAsStateWithLifecycle()
    val tapSteps by viewModel.tapSteps.collectAsStateWithLifecycle()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var pendingExportJson by remember { mutableStateOf<String?>(null) }
    val createJsonLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.CreateDocument("application/json")
    ) { uri: Uri? ->
        val json = pendingExportJson
        pendingExportJson = null
        if (uri != null && json != null) {
            val wrote = writeTextToUri(context, uri, json)
            scope.launch {
                snackbarHostState.showSnackbar(
                    if (wrote) "Exported to file" else "Export failed"
                )
            }
        }
    }

    val hasStoppedSession = !isRecording && sessionStartMs != null && sessionEndMs != null

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text("UI Action Recorder") },
                actions = {
                    OutlinedButton(onClick = onOpenActionCapture, modifier = Modifier.padding(end = 8.dp)) {
                        Text("Action Capture")
                    }
                    OutlinedButton(onClick = onOpenReview, modifier = Modifier.padding(end = 8.dp)) {
                        Text("Tap Review (${tapSteps.size})")
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

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("Full tree capture on screen change")
                Switch(
                    checked = fullTreeCaptureEnabled,
                    onCheckedChange = { viewModel.setFullTreeCaptureEnabled(it) },
                    enabled = !isRecording
                )
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("Raw touch capture (experimental)")
                Switch(
                    checked = rawTouchCaptureEnabled,
                    onCheckedChange = { viewModel.setRawTouchCaptureEnabled(it) },
                    colors = SwitchDefaults.colors(checkedTrackColor = MaterialTheme.colorScheme.error)
                )
            }
            if (rawTouchCaptureEnabled) {
                Text(
                    "Warning: this has caused touch input to freeze system-wide on some devices " +
                        "(observed on MIUI/HyperOS). Only power button + fingerprint kept working. " +
                        "Test on a device you can reboot, not your daily phone.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }

            Button(
                onClick = { if (isRecording) viewModel.stopRecording() else viewModel.startRecording() },
                enabled = accessibilityEnabled,
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (isRecording) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
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

            Spacer(modifier = Modifier.height(8.dp))

            if (hasStoppedSession) {
                SessionSummary(
                    events = events,
                    startMs = sessionStartMs!!,
                    endMs = sessionEndMs!!,
                    onCopyReport = {
                        val session = RecordingSession(sessionStartMs!!, sessionEndMs!!, events)
                        copyToClipboard(context, "UI Recorder Report", ReportFormatter.toPlainText(session))
                        scope.launch { snackbarHostState.showSnackbar("Copied ${events.size} events to clipboard") }
                    },
                    onCopyJson = {
                        val session = RecordingSession(sessionStartMs!!, sessionEndMs!!, events)
                        copyToClipboard(context, "UI Recorder JSON", ReportFormatter.toJson(session))
                        scope.launch { snackbarHostState.showSnackbar("Copied JSON for ${events.size} events") }
                    },
                    onExportJsonFile = {
                        val session = RecordingSession(sessionStartMs!!, sessionEndMs!!, events)
                        pendingExportJson = ReportFormatter.toJson(session)
                        createJsonLauncher.launch(defaultExportFileName(sessionStartMs!!))
                    }
                )
                HorizontalDivider()
            }

            if (events.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        if (isRecording) {
                            "Waiting for events… switch to another app and interact with it."
                        } else {
                            "No events yet. Press Start Recording, then switch to another app."
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(32.dp)
                    )
                }
            } else {
                LazyColumn(modifier = Modifier.weight(1f, fill = true)) {
                    itemsIndexed(events, key = { index, _ -> index }) { _, event ->
                        EventRow(event)
                    }
                }
            }
        }
    }
}

@Composable
private fun AccessibilityStatusRow(enabled: Boolean, onEnableClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(12.dp)
                    .clip(CircleShape)
                    .background(if (enabled) Color(0xFF2E7D32) else Color(0xFFC62828))
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(if (enabled) "Accessibility service enabled" else "Accessibility service disabled")
        }
        if (!enabled) {
            Button(onClick = onEnableClick) {
                Text("Enable")
            }
        }
    }
}

@Composable
private fun SessionSummary(
    events: List<UiEvent>,
    startMs: Long,
    endMs: Long,
    onCopyReport: () -> Unit,
    onCopyJson: () -> Unit,
    onExportJsonFile: () -> Unit
) {
    val durationSec = (endMs - startMs).coerceAtLeast(0) / 1000
    val packages = events.map { it.packageName }.distinct()

    Column(modifier = Modifier.padding(16.dp)) {
        Text("Session summary", fontWeight = FontWeight.Bold)
        Text("${events.size} events · ${durationSec}s · ${packages.size} app(s): ${packages.joinToString(", ")}")
        Spacer(modifier = Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onCopyReport) { Text("Copy report") }
            OutlinedButton(onClick = onCopyJson) { Text("Copy raw JSON") }
        }
        Spacer(modifier = Modifier.height(8.dp))
        OutlinedButton(onClick = onExportJsonFile, modifier = Modifier.fillMaxWidth()) {
            Text("Export as JSON file")
        }
    }
}

@Composable
private fun EventRow(event: UiEvent) {
    var expanded by remember { mutableStateOf(false) }
    val timeFormatter = remember { SimpleDateFormat("HH:mm:ss.SSS", Locale.US) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { expanded = !expanded }
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        val resourceLabel = event.source?.resourceId ?: "(no resource-id)"
        val summaryText = event.eventText.firstOrNull()
            ?: event.source?.contentDescription
            ?: event.source?.text
            ?: ""
        Text(
            "[${timeFormatter.format(event.timestampWallClockMs)}] ${event.eventType} — $resourceLabel — $summaryText",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium
        )
        Text(
            event.packageName,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        AnimatedVisibility(visible = expanded) {
            EventDetail(event)
        }
    }
    HorizontalDivider()
}

@Composable
private fun EventDetail(event: UiEvent) {
    Column(modifier = Modifier.padding(top = 8.dp)) {
        DetailLine("packageName", event.packageName)
        DetailLine("timestampWallClockMs", event.timestampWallClockMs.toString())
        DetailLine("timestampMonotonicMs (boot-relative)", event.timestampMonotonicMs.toString())
        DetailLine("eventText", event.eventText.toString())
        event.scrollDeltaX?.let { DetailLine("scrollDeltaX", it.toString()) }
        event.scrollDeltaY?.let { DetailLine("scrollDeltaY", it.toString()) }
        event.scrollX?.let { DetailLine("scrollX", it.toString()) }
        event.scrollY?.let { DetailLine("scrollY", it.toString()) }
        event.beforeText?.let { DetailLine("beforeText", it) }
        event.addedCount?.let { DetailLine("addedCount", it.toString()) }
        event.removedCount?.let { DetailLine("removedCount", it.toString()) }

        val source = event.source
        if (source == null) {
            DetailLine("source", "(null — no source node for this event)")
        } else {
            DetailLine("resourceId", source.resourceId ?: "(none)")
            DetailLine("className", source.className ?: "(none)")
            DetailLine("text", source.text ?: "(none)")
            DetailLine("contentDescription", source.contentDescription ?: "(none)")
            DetailLine("hintText", source.hintText ?: "(none)")
            source.bounds?.let {
                DetailLine("bounds", "(${it.left},${it.top})-(${it.right},${it.bottom})")
            }
            DetailLine("isClickable", source.isClickable.toString())
            DetailLine("isCheckable", source.isCheckable.toString())
            DetailLine("isChecked", source.isChecked.toString())
            DetailLine("isEnabled", source.isEnabled.toString())
            DetailLine("isFocused", source.isFocused.toString())
            DetailLine("isScrollable", source.isScrollable.toString())
            DetailLine("isEditable", source.isEditable.toString())
            DetailLine("isPassword", source.isPassword.toString())
            DetailLine("isLongClickable", source.isLongClickable.toString())
            DetailLine("childCount", source.childCount?.toString() ?: "(none)")
            source.ancestors?.let { ancestors ->
                DetailLine(
                    "ancestors",
                    ancestors.joinToString(" > ") { "${it.className ?: "?"}(${it.resourceId ?: "none"})" }
                )
            }
        }

        event.fullTreeSnapshot?.let { tree ->
            DetailLine("fullTreeSnapshot", "${tree.size} nodes captured")
        }
    }
}

@Composable
private fun DetailLine(key: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth()) {
        Text("$key: ", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}

private fun copyToClipboard(context: Context, label: String, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText(label, text))
}

private fun writeTextToUri(context: Context, uri: Uri, text: String): Boolean {
    return try {
        context.contentResolver.openOutputStream(uri)?.use { it.write(text.toByteArray()) }
        true
    } catch (e: Exception) {
        false
    }
}

private fun defaultExportFileName(sessionStartMs: Long): String {
    val stamp = SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(Date(sessionStartMs))
    return "ui_recording_$stamp.json"
}
