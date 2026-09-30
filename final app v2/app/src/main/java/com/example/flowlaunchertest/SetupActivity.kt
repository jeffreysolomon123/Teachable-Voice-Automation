package com.example.flowlaunchertest

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import org.json.JSONException
import org.json.JSONObject

/** Device setup: backend URL, permissions, and a manual Slot JSON start for debugging. */
class SetupActivity : ComponentActivity() {

    companion object {
        private const val TAG = "SetupActivity"

        // The Slot JSON normally comes from the voice/intent stage; this example is editable here.
        private val EXAMPLE_SLOTS = """
            {
              "mode": "ORDER",
              "stage": "STAGE_2_SLOTS",
              "flow_id": "order_dominos_zomato",
              "app_name": "Zomato",
              "summary": "Order a Margherita pizza from Domino's on Zomato",
              "confirmed": true,
              "slots": {
                "item": "Margherita pizza",
                "restaurant": "Domino's",
                "app": "Zomato",
                "quantity": 1,
                "address": "Home",
                "customizations": null
              },
              "metadata": {"requires_user_confirmation": false}
            }
        """.trimIndent()
    }

    private var overlayGranted by mutableStateOf(false)
    private var accessibilityEnabled by mutableStateOf(false)
    private var micGranted by mutableStateOf(false)

    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted -> micGranted = granted }

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) Log.w(TAG, "POST_NOTIFICATIONS denied; foreground-service notification will be hidden")
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.clearLegacySecrets(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) { Screen() }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        overlayGranted = Settings.canDrawOverlays(this)
        accessibilityEnabled = WindowWatcherAccessibilityService.isEnabledInSettings(this)
        micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
    }

    @Composable
    private fun Screen() {
        var backendUrl by remember { mutableStateOf(Prefs.backendUrl(this)) }
        var slotJson by remember { mutableStateOf(Prefs.slotJson(this) ?: EXAMPLE_SLOTS) }
        Column(
            Modifier
                .safeDrawingPadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Device setup", style = MaterialTheme.typography.headlineSmall)
            OutlinedTextField(
                value = backendUrl,
                onValueChange = { backendUrl = it },
                label = { Text("Backend URL") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = {
                Prefs.setBackendUrl(this@SetupActivity, backendUrl)
                finish()
            }) { Text("Save & back to assistant") }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Permissions", style = MaterialTheme.typography.titleMedium)
            StatusRow(ok = overlayGranted) {
                OutlinedButton(onClick = {
                    startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
                }) { Text("Grant overlay permission") }
            }
            StatusRow(ok = micGranted) {
                OutlinedButton(onClick = { micPermission.launch(Manifest.permission.RECORD_AUDIO) }) {
                    Text("Allow microphone")
                }
            }
            StatusRow(ok = accessibilityEnabled) {
                OutlinedButton(onClick = { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }) {
                    Text("Enable accessibility service")
                }
            }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Debug: start a replay from Slot JSON (bypasses the voice assistant)",
                 style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                value = slotJson,
                onValueChange = { slotJson = it },
                label = { Text("Slot JSON") },
                textStyle = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 12.sp),
                modifier = Modifier.fillMaxWidth().heightIn(min = 200.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = { onStart(backendUrl, slotJson) }) { Text("Start replay") }
                OutlinedButton(onClick = { ReplayController.stop() }) { Text("Stop") }
            }
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                Text("Android ${Build.VERSION.RELEASE}: text input needs Android 13+; the backend will ask you to type.",
                     color = Color(0xFFC62828))
            }
        }
    }

    @Composable
    private fun StatusRow(ok: Boolean, content: @Composable () -> Unit) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(14.dp).background(if (ok) Color(0xFF2E7D32) else Color(0xFFC62828), CircleShape))
            Spacer(Modifier.size(12.dp))
            content()
        }
    }

    private fun onStart(backendUrl: String, slotJson: String) {
        ReplayController.preflight(this)?.let { return toast(it) }
        val payload = try {
            JSONObject(slotJson)
        } catch (e: JSONException) {
            return toast("Slot JSON is invalid: ${e.message}")
        }
        Prefs.setBackendUrl(this, backendUrl)
        Prefs.setSlotJson(this, slotJson)
        ReplayController.start(applicationContext, backendUrl, payload)
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
    }
}
