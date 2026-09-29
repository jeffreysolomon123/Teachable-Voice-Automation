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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    private var overlayGranted by mutableStateOf(false)
    private var accessibilityEnabled by mutableStateOf(false)
    private var apiKeySaved by mutableStateOf(false)

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                Log.w(TAG, "POST_NOTIFICATIONS denied; foreground-service notification will be hidden")
                toast("Notification permission denied — overlay still works, its notification is just hidden")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
        apiKeySaved = Prefs.groqApiKey(this).isNotBlank()
    }

    @Composable
    private fun Screen() {
        var keyInput by remember { mutableStateOf(Prefs.groqApiKey(this)) }
        Column(
            Modifier
                .safeDrawingPadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("FlowLauncherTest", style = MaterialTheme.typography.headlineSmall)
            TARGET_APPS.forEach { app ->
                Button(onClick = { onAppTapped(app) }, modifier = Modifier.fillMaxWidth()) {
                    Text(app.name)
                }
            }

            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Text("Settings", style = MaterialTheme.typography.titleMedium)

            OutlinedTextField(
                value = keyInput,
                onValueChange = { keyInput = it },
                label = { Text("Groq API key") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
            )
            StatusRow(ok = apiKeySaved) {
                OutlinedButton(onClick = {
                    Prefs.setGroqApiKey(this@MainActivity, keyInput)
                    apiKeySaved = keyInput.isNotBlank()
                    toast(if (apiKeySaved) "API key saved" else "API key cleared")
                }) { Text("Save API key") }
            }
            StatusRow(ok = overlayGranted) {
                OutlinedButton(onClick = {
                    startActivity(
                        Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
                    )
                }) { Text("Grant overlay permission") }
            }
            StatusRow(ok = accessibilityEnabled) {
                OutlinedButton(onClick = {
                    startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                }) { Text("Enable accessibility service") }
            }
        }
    }

    @Composable
    private fun StatusRow(ok: Boolean, content: @Composable () -> Unit) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(14.dp)
                    .background(if (ok) Color(0xFF2E7D32) else Color(0xFFC62828), CircleShape)
            )
            Spacer(Modifier.size(12.dp))
            content()
        }
    }

    private fun onAppTapped(app: TargetApp) {
        overlayGranted = Settings.canDrawOverlays(this)
        accessibilityEnabled = WindowWatcherAccessibilityService.isEnabledInSettings(this)

        val missing = buildList {
            if (!overlayGranted) add("overlay permission")
            if (!accessibilityEnabled) add("accessibility service")
        }
        if (missing.isNotEmpty()) {
            Log.w(TAG, "Cannot launch ${app.name}: missing ${missing.joinToString()}")
            toast("Missing: ${missing.joinToString(" and ")}")
            return
        }
        if (!WindowWatcherAccessibilityService.isConnected) {
            Log.w(TAG, "Accessibility service enabled in settings but not connected")
            toast("Accessibility service is enabled but not running — toggle it off and on in Settings")
            return
        }
        if (Prefs.groqApiKey(this).isBlank()) {
            Log.w(TAG, "Cannot launch ${app.name}: Groq API key missing")
            toast("Groq API key missing — paste it and tap Save first")
            return
        }

        val launchIntent = packageManager.getLaunchIntentForPackage(app.pkg)
            ?.apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
        if (launchIntent == null) {
            Log.w(TAG, "${app.pkg} not installed (no launch intent)")
            OverlayBus.state.value = OverlayState.NotInstalled(app.name)
            OverlayService.start(this)
            return
        }

        Log.i(TAG, "Launching ${app.pkg}")
        OverlayBus.state.value = OverlayState.Launching(app.name)
        OverlayService.start(this)
        WindowWatcherAccessibilityService.watched.value =
            WindowWatcherAccessibilityService.WatchRequest(app.pkg, app.name)
        startActivity(launchIntent)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
