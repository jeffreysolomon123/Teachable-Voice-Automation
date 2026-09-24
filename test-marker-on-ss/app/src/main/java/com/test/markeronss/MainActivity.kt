package com.test.markeronss

import android.content.Intent
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Heartbeat.start()
        setContent {
            MaterialTheme {
                MainScreen(onOpenSettings = { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) })
            }
        }
    }

    // Clicks inside this app (toggle, gallery) are not captured.
    override fun onResume() {
        super.onResume()
        AppState.appInForeground = true
    }

    override fun onPause() {
        AppState.appInForeground = false
        super.onPause()
    }
}

@Composable
private fun MainScreen(onOpenSettings: () -> Unit) {
    val context = LocalContext.current
    val store = remember { CaptureStore(context.applicationContext) }
    val connected by AppState.connected.collectAsState()
    val watching by AppState.watching.collectAsState()
    val version by AppState.capturesVersion.collectAsState()
    var clearedAt by remember { mutableStateOf(0) }
    var viewing by remember { mutableStateOf<Capture?>(null) }

    val captures by produceState(emptyList<Capture>(), version, clearedAt) {
        value = withContext(Dispatchers.IO) { store.load() }
    }

    viewing?.let { c ->
        FullSizeViewer(store, c, onClose = { viewing = null })
        return
    }

    LazyColumn(
        Modifier
            .safeDrawingPadding()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item { Text("Marker on screenshot", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 12.dp)) }

        item { Text(if (connected) "Accessibility service: CONNECTED" else "Accessibility service: NOT connected") }
        if (!connected) {
            item { OutlinedButton(onClick = onOpenSettings) { Text("Open accessibility settings") } }
        }

        item {
            Button(
                enabled = connected,
                onClick = {
                    val svc = MarkerAccessibilityService.instance
                    if (watching) svc?.stopWatching() else svc?.startWatching()
                },
                colors = if (watching) ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error) else ButtonDefaults.buttonColors(),
                modifier = Modifier.fillMaxWidth()
            ) { Text(if (watching) "Stop watching" else "Start watching") }
        }

        item { HeartbeatAndCounters() }

        item {
            OutlinedButton(onClick = {
                store.clear()
                AppState.saved.value = 0
                AppState.failed.value = 0
                AppState.dropped.value = 0
                AppState.throttled.set(0)
                AppState.clicksCaptured.value = 0
                clearedAt++
            }) { Text("Clear gallery and counters") }
        }

        item { Text("Gallery (newest first, tap to view full size)", style = MaterialTheme.typography.titleMedium) }
        if (captures.isEmpty()) item { Text("Nothing captured yet.") }
        items(captures) { c -> CaptureRow(store, c, onClick = { if (c.ok) viewing = c }) }
    }
}

@Composable
private fun HeartbeatAndCounters() {
    val beat by Heartbeat.count.collectAsState()
    val stall by Heartbeat.worstStallMs.collectAsState()
    val clicks by AppState.clicksCaptured.collectAsState()
    val saved by AppState.saved.collectAsState()
    val failed by AppState.failed.collectAsState()
    val dropped by AppState.dropped.collectAsState()
    val status by AppState.status.collectAsState()

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("♥ heartbeat: $beat", fontSize = 28.sp, fontFamily = FontFamily.Monospace)
            Text("worst tick stall: $stall ms (resets on Start)", fontFamily = FontFamily.Monospace)
            // Recomposes every second with the heartbeat, which is what refreshes this atomic.
            val throttled = AppState.throttled.get()
            Text("clicks captured: $clicks   saved: $saved   failed/blocked: $failed")
            Text("ignored (too fast): $throttled   dropped: $dropped")
            Text(status, style = MaterialTheme.typography.bodySmall)
        }
    }
}

private val timeFmt = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)

@Composable
private fun CaptureRow(store: CaptureStore, c: Capture, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (c.ok) {
                val thumb by produceState<ImageBitmap?>(null, c.fileName) {
                    value = withContext(Dispatchers.IO) {
                        val opts = BitmapFactory.Options().apply { inSampleSize = 4 }
                        BitmapFactory.decodeFile(store.file(c.fileName!!).absolutePath, opts)?.asImageBitmap()
                    }
                }
                Box(Modifier.width(90.dp).height(200.dp).background(Color.LightGray)) {
                    thumb?.let {
                        Image(it, "Captured screenshot", contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
                    }
                }
            } else {
                Box(
                    Modifier.width(90.dp).height(90.dp).background(MaterialTheme.colorScheme.errorContainer),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        if (c.failure!!.startsWith("BLOCKED")) "BLOCKED" else "FAILED",
                        color = MaterialTheme.colorScheme.onErrorContainer
                    )
                }
            }
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(timeFmt.format(Date(c.timeMs)), fontFamily = FontFamily.Monospace)
                Text("click in ${c.pkg}")
                if (c.ok) {
                    Text("${c.width}x${c.height}, click→shot ${c.latencyMs} ms", style = MaterialTheme.typography.bodySmall)
                    c.note?.let {
                        Text("NO MARKER: $it", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                    }
                } else {
                    Text(c.failure!!, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun FullSizeViewer(store: CaptureStore, c: Capture, onClose: () -> Unit) {
    BackHandler(onBack = onClose)
    val image by produceState<ImageBitmap?>(null, c.fileName) {
        value = withContext(Dispatchers.IO) {
            BitmapFactory.decodeFile(store.file(c.fileName!!).absolutePath)?.asImageBitmap()
        }
    }
    Box(Modifier.fillMaxSize().background(Color.Black).clickable(onClick = onClose)) {
        image?.let {
            Image(it, "Captured screenshot, full size", contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
        }
        Text(
            "${c.pkg} · ${timeFmt.format(Date(c.timeMs))} · tap to close",
            color = Color.White,
            modifier = Modifier.align(Alignment.BottomCenter).background(Color(0x99000000)).padding(8.dp)
        )
    }
}
