package com.uirecorder.app.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.background
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.uirecorder.app.data.ReviewJudgment
import com.uirecorder.app.data.TapStepRecord
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TapReviewScreen(viewModel: RecorderViewModel, onBack: () -> Unit) {
    val steps by viewModel.tapSteps.collectAsStateWithLifecycle()
    var index by remember { mutableIntStateOf(0) }

    LaunchedEffect(steps.size) {
        if (index >= steps.size) index = (steps.size - 1).coerceAtLeast(0)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Tap Capture Review") },
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
            TallyBar(steps)
            HorizontalDivider()

            if (steps.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        "No tap steps captured yet. Record a session with taps on another app.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(32.dp)
                    )
                }
                return@Scaffold
            }

            val step = steps[index]

            StepPager(
                index = index,
                total = steps.size,
                onPrev = { if (index > 0) index-- },
                onNext = { if (index < steps.size - 1) index++ }
            )
            HorizontalDivider()

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(12.dp)
            ) {
                ScreenshotWithOverlay(step, modifier = Modifier.fillMaxWidth())
                Spacer(modifier = Modifier.height(12.dp))
                AccessibilityFingerprint(step)
                Spacer(modifier = Modifier.height(12.dp))
                JudgmentRow(step, onJudge = { viewModel.setTapJudgment(step.stepIndex, it) })
            }
        }
    }
}

@Composable
private fun TallyBar(steps: List<TapStepRecord>) {
    val yes = steps.count { it.reviewJudgment == ReviewJudgment.YES }
    val partial = steps.count { it.reviewJudgment == ReviewJudgment.PARTIAL }
    val no = steps.count { it.reviewJudgment == ReviewJudgment.NO }
    val unreviewed = steps.size - yes - partial - no

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        TallyItem("Yes", yes, Color(0xFF2E7D32))
        TallyItem("Partial", partial, Color(0xFFF9A825))
        TallyItem("No", no, Color(0xFFC62828))
        TallyItem("Unreviewed", unreviewed, MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun TallyItem(label: String, count: Int, color: Color) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(count.toString(), fontWeight = FontWeight.Bold, color = color)
        Text(label, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

@Composable
private fun StepPager(index: Int, total: Int, onPrev: () -> Unit, onNext: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        OutlinedButton(onClick = onPrev, enabled = index > 0) { Text("< Prev") }
        Text("Step ${index + 1} / $total", fontWeight = FontWeight.Medium)
        OutlinedButton(onClick = onNext, enabled = index < total - 1) { Text("Next >") }
    }
}

@Composable
private fun ScreenshotWithOverlay(step: TapStepRecord, modifier: Modifier = Modifier) {
    if (step.screenshotBlocked) {
        val reason = step.screenshotBlockedReason
        val message = when (reason) {
            "secure_window" -> "screenshotBlocked: true (secure_window) — FLAG_SECURE, a real credential-boundary signal"
            "blank_pixels" -> "screenshotBlocked: true (blank_pixels) — capture \"succeeded\" but returned a blank frame, usually also FLAG_SECURE"
            "rate_limited" -> "screenshotBlocked: true (rate_limited) — NOT a security signal, just called takeScreenshot() too soon after a previous one"
            else -> "screenshotBlocked: true (${reason ?: "unknown"})"
        }
        NoticeBanner(message, modifier = modifier)
        return
    }

    val path = step.screenshotPath
    if (path == null) {
        NoticeBanner("No screenshot captured for this step.", modifier = modifier)
        return
    }

    val imageBitmap = remember(path) { BitmapFactory.decodeFile(path)?.asImageBitmap() }
    if (imageBitmap == null) {
        NoticeBanner("Failed to load screenshot from disk.", modifier = modifier)
        return
    }

    val bmpWidth = imageBitmap.width.toFloat()
    val bmpHeight = imageBitmap.height.toFloat()

    Box(modifier = modifier) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(bmpWidth / bmpHeight)
                .clip(RoundedCornerShape(8.dp))
        ) {
            val scaleX = size.width / bmpWidth
            val scaleY = size.height / bmpHeight

            drawImage(
                image = imageBitmap,
                dstSize = androidx.compose.ui.unit.IntSize(size.width.roundToInt(), size.height.roundToInt())
            )

            for (candidate in step.ocrCandidates) {
                val b = candidate.bounds
                val topLeft = Offset(b.left * scaleX, b.top * scaleY)
                val boxSize = Size((b.right - b.left) * scaleX, (b.bottom - b.top) * scaleY)
                drawRect(
                    color = Color(0xFF2979FF),
                    topLeft = topLeft,
                    size = boxSize,
                    style = Stroke(width = 2f)
                )
                drawContext.canvas.nativeCanvas.drawText(
                    candidate.text.take(24),
                    topLeft.x,
                    (topLeft.y - 4f).coerceAtLeast(10f),
                    android.graphics.Paint().apply {
                        color = android.graphics.Color.rgb(41, 121, 255)
                        textSize = 24f
                        isAntiAlias = true
                    }
                )
            }

            step.touchPoint?.let { tp ->
                val cx = tp.x * scaleX
                val cy = tp.y * scaleY
                drawLine(Color.Red, Offset(cx - 24, cy), Offset(cx + 24, cy), strokeWidth = 4f)
                drawLine(Color.Red, Offset(cx, cy - 24), Offset(cx, cy + 24), strokeWidth = 4f)
                drawCircle(Color.Red, radius = 28f, center = Offset(cx, cy), style = Stroke(width = 3f))
            }
        }

        if (step.touchPoint == null) {
            Surface(
                color = MaterialTheme.colorScheme.errorContainer,
                shape = RoundedCornerShape(6.dp),
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = 8.dp)
            ) {
                Text(
                    text = if (!step.touchCaptureSupported) {
                        "No touch location known (below API 34, or raw touch capture isn't enabled)"
                    } else {
                        "No touch location known (no motion event matched this tap)"
                    },
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)
                )
            }
        }
    }
}

@Composable
private fun NoticeBanner(text: String, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(160.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center
    ) {
        Text(text, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
    }
}

@Composable
private fun AccessibilityFingerprint(step: TapStepRecord) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            "Step ${step.stepIndex} · ${step.eventType} · ${step.packageName}",
            fontWeight = FontWeight.Bold
        )
        Spacer(modifier = Modifier.height(4.dp))

        val node = step.accessibilityNode
        if (node == null) {
            Text(
                "accessibility source: none (event.source was null for this tap)",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall
            )
            if (step.eventText.isNotEmpty()) {
                Text(
                    "but event.text (the event's own announcement, not the node) had: ${step.eventText.joinToString(" | ") { "\"$it\"" }}",
                    color = MaterialTheme.colorScheme.tertiary,
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.Medium
                )
            }
        } else {
            FingerprintLine("resourceId", node.resourceId ?: "(none)")
            FingerprintLine("text", node.text ?: "(none)")
            FingerprintLine("contentDescription", node.contentDescription ?: "(none)")
            if (step.eventText.isNotEmpty()) {
                FingerprintLine("eventText", step.eventText.joinToString(" | ") { "\"$it\"" })
            }
        }

        Spacer(modifier = Modifier.height(4.dp))
        FingerprintLine(
            "touchPoint",
            step.touchPoint?.let { "(${it.x.toInt()}, ${it.y.toInt()})" } ?: "null"
        )
        FingerprintLine("touchCaptureSupported", step.touchCaptureSupported.toString())
        FingerprintLine(
            "ocrMethodUsed",
            step.ocrMethodUsed ?: if (step.ocrProcessed) "none (no OCR text found)" else "pending…"
        )
        FingerprintLine("ocrCandidates", step.ocrCandidates.size.toString())

        val nearest = step.ocrCandidates.minByOrNull { it.distanceFromTouchPoint ?: Float.MAX_VALUE }
        if (nearest != null) {
            FingerprintLine(
                "nearest OCR candidate",
                "\"${nearest.text}\" (${nearest.distanceFromTouchPoint?.roundToInt() ?: "-"}px from touch point)"
            )
        }
    }
}

@Composable
private fun FingerprintLine(key: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth()) {
        Text("$key: ", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun JudgmentRow(step: TapStepRecord, onJudge: (ReviewJudgment) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            "Was there an OCR candidate near the touch point that matches what was actually tapped?",
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium
        )
        Spacer(modifier = Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            JudgmentButton("Yes", Color(0xFF2E7D32), step.reviewJudgment == ReviewJudgment.YES) {
                onJudge(ReviewJudgment.YES)
            }
            JudgmentButton("Partial", Color(0xFFF9A825), step.reviewJudgment == ReviewJudgment.PARTIAL) {
                onJudge(ReviewJudgment.PARTIAL)
            }
            JudgmentButton("No", Color(0xFFC62828), step.reviewJudgment == ReviewJudgment.NO) {
                onJudge(ReviewJudgment.NO)
            }
        }
    }
}

@Composable
private fun JudgmentButton(label: String, color: Color, selected: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) color else MaterialTheme.colorScheme.surfaceVariant,
            contentColor = if (selected) Color.White else MaterialTheme.colorScheme.onSurfaceVariant
        )
    ) {
        Text(label)
    }
}
