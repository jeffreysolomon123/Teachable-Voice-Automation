package com.test.touchcapture2

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.TextUtils
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                        UnsupportedApiScreen(actualSdkInt = Build.VERSION.SDK_INT)
                    } else {
                        TouchCaptureScreen(
                            isAccessibilityEnabled = { isAccessibilityServiceEnabled() },
                            onOpenAccessibilitySettings = {
                                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                            }
                        )
                    }
                }
            }
        }
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val expectedComponent = "$packageName/${TouchCaptureAccessibilityService::class.java.name}"
        val enabledServices = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val splitter = TextUtils.SimpleStringSplitter(':')
        splitter.setString(enabledServices)
        while (splitter.hasNext()) {
            if (splitter.next().equals(expectedComponent, ignoreCase = true)) {
                return true
            }
        }
        return false
    }
}

@Composable
fun UnsupportedApiScreen(actualSdkInt: Int) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Touch Capture Test 2", style = MaterialTheme.typography.headlineSmall)
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Not supported on this device", fontWeight = FontWeight.Bold)
                Text(
                    "This device's Android version does not support this feature — API 34+ " +
                        "required, this device is API $actualSdkInt."
                )
            }
        }
    }
}

@Composable
fun TouchCaptureScreen(
    isAccessibilityEnabled: () -> Boolean,
    onOpenAccessibilitySettings: () -> Unit
) {
    var heartbeat by remember { mutableIntStateOf(0) }
    var accessibilityEnabled by remember { mutableStateOf(isAccessibilityEnabled()) }
    val isWatching by TouchCaptureState.isWatching.collectAsState()
    val touches by TouchCaptureState.touches.collectAsState()
    val totalCount by TouchCaptureState.totalDownCount.collectAsState()
    val moveEventsIgnored by TouchCaptureState.moveEventsIgnoredCount.collectAsState()
    val rateLimitTriggeredCount by TouchCaptureState.rateLimitTriggeredCount.collectAsState()
    val cooldownActive by TouchCaptureState.rateLimitCooldownActive.collectAsState()

    // Heartbeat: increments once per second on the main thread. If this ever stops
    // updating on screen, the UI thread is hung. This is the primary stability signal.
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            heartbeat++
        }
    }

    // Poll accessibility-enabled status so the UI updates after the user returns
    // from Settings without needing to restart the activity.
    LaunchedEffect(Unit) {
        while (true) {
            accessibilityEnabled = isAccessibilityEnabled()
            delay(1000)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Touch Capture Test 2", style = MaterialTheme.typography.headlineSmall)

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("Heartbeat (hang detector)", fontWeight = FontWeight.Bold)
                Text(
                    text = heartbeat.toString(),
                    style = MaterialTheme.typography.displayMedium
                )
                Text("Increments every second. If it freezes, the UI thread is hung.")
            }
        }

        if (!accessibilityEnabled) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Accessibility service is not enabled.", fontWeight = FontWeight.Bold)
                    Text("Enable \"TouchCaptureTest2\" under Accessibility settings, then come back here.")
                    Button(onClick = onOpenAccessibilitySettings) {
                        Text("Open Accessibility Settings")
                    }
                }
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Button(
                    onClick = {
                        TouchCaptureState.resetCounts()
                        TouchCaptureState.watchRequested.value = true
                    },
                    enabled = !isWatching
                ) {
                    Text("Start Watching")
                }
                Button(
                    onClick = {
                        TouchCaptureState.watchRequested.value = false
                    },
                    enabled = isWatching
                ) {
                    Text("Stop Watching")
                }
            }
            Text(if (isWatching) "Status: watching (raw ACTION_DOWN via onMotionEvent)" else "Status: stopped")
        }

        if (cooldownActive) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    modifier = Modifier.padding(16.dp),
                    text = "Rate limit cooldown active — more than 50 ACTION_DOWN events were " +
                        "seen within one second. Processing is paused briefly as a safety net."
                )
            }
        }

        Text("Down-events captured: $totalCount")
        Text("Rate-limit cooldowns triggered: $rateLimitTriggeredCount")
        Text("ACTION_MOVE events seen but ignored (temporary diagnostic): $moveEventsIgnored")

        HorizontalDivider()

        Text("Last ${touches.size} touches (newest first):", fontWeight = FontWeight.Bold)
        LazyColumn(
            modifier = Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            items(touches) { touch ->
                TouchRow(touch)
            }
        }
    }
}

@Composable
private fun TouchRow(touch: TouchRecord) {
    val timeFormat = remember { SimpleDateFormat("HH:mm:ss.SSS", Locale.US) }
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(8.dp),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text("x=${touch.x.toInt()}, y=${touch.y.toInt()}")
            Text(timeFormat.format(Date(touch.timestampMs)))
        }
    }
}
