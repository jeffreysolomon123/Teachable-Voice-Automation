package com.test.screenshotdiff

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ScreenshotDiffScreen()
                }
            }
        }
    }
}

@Composable
fun ScreenshotDiffScreen() {
    val isWatching by TapCaptureState.isWatching.collectAsState()
    val heartbeat by TapCaptureState.heartbeat.collectAsState()
    val tapCount by TapCaptureState.tapCount.collectAsState()
    val capturedTaps by TapCaptureState.capturedTaps.collectAsState()
    val context = LocalContext.current

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Heartbeat: $heartbeat", style = MaterialTheme.typography.headlineSmall)
        Spacer(modifier = Modifier.height(4.dp))
        Text("Taps captured: $tapCount", style = MaterialTheme.typography.bodyLarge)
        Spacer(modifier = Modifier.height(12.dp))

        Row {
            Button(onClick = { TapCaptureState.isWatching.value = !isWatching }) {
                Text(if (isWatching) "Stop Watching" else "Start Watching")
            }
            Spacer(modifier = Modifier.width(8.dp))
            OutlinedButton(onClick = {
                context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }) {
                Text("Accessibility Settings")
            }
        }

        Spacer(modifier = Modifier.height(16.dp))
        Text("Review (${capturedTaps.size})", style = MaterialTheme.typography.titleMedium)
        Spacer(modifier = Modifier.height(8.dp))

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(capturedTaps.asReversed(), key = { it.id }) { tap ->
                TapReviewCard(tap)
                Spacer(modifier = Modifier.height(16.dp))
            }
        }
    }
}

@Composable
fun TapReviewCard(tap: CapturedTap) {
    val imageBitmap = remember(tap.id) { tap.displayBitmap.asImageBitmap() }
    val boxWidth = tap.displayBitmap.width.toFloat()
    val boxHeight = tap.displayBitmap.height.toFloat()

    Column(modifier = Modifier.fillMaxWidth()) {
        Text("Tap #${tap.id} — confidence: ${tap.confidence}")
        Spacer(modifier = Modifier.height(4.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(boxWidth / boxHeight)
        ) {
            Image(
                bitmap = imageBitmap,
                contentDescription = null,
                modifier = Modifier.fillMaxSize()
            )
            Canvas(modifier = Modifier.fillMaxSize()) {
                val region = tap.region ?: return@Canvas
                val scaleX = size.width / boxWidth
                val scaleY = size.height / boxHeight
                drawRect(
                    color = if (tap.confidence == "high") Color.Green else Color.Yellow,
                    topLeft = Offset(region.left * scaleX, region.top * scaleY),
                    size = Size(
                        (region.right - region.left) * scaleX,
                        (region.bottom - region.top) * scaleY
                    ),
                    style = Stroke(width = 4.dp.toPx())
                )
            }
        }
    }
}
