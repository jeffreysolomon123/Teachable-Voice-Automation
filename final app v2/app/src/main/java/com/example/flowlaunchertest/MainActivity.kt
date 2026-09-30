package com.example.flowlaunchertest

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import org.json.JSONException
import org.json.JSONObject

/**
 * The voice assistant (assistant/mobile_assistant_app UI, served by the backend at /mobile/) in a
 * WebView, plus a native bridge that executes confirmed plans with the visual replay engine.
 *
 *   voice -> backend /api/voice (STT, orchestrator, TTS) -> confirmed plan (Slot JSON)
 *         -> AndroidBridge.startReplay(plan) -> ReplayController -> backend /flows/match + /replay/{start,step,confirm}
 */
class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    private lateinit var webView: WebView
    private var loadedUrl: String? = null
    private var pendingMicRequest: PermissionRequest? = null

    private val permissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            val mic = granted[Manifest.permission.RECORD_AUDIO] == true || hasMic()
            pendingMicRequest?.let { req ->
                if (mic) req.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else req.deny()
            }
            pendingMicRequest = null
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Prefs.clearLegacySecrets(this)

        webView = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#08090D"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.allowFileAccess = false
            addJavascriptInterface(Bridge(), "AndroidBridge")
            webViewClient = object : WebViewClient() {
                // AndroidBridge must only ever be reachable from the backend's own pages.
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val backend = Uri.parse(Prefs.backendUrl(this@MainActivity))
                    val u = request.url
                    if (u.scheme == backend.scheme && u.host == backend.host && u.port == backend.port) return false
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, u)) }
                    return true
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) showOfflinePage(error.description?.toString() ?: "unreachable")
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    runOnUiThread { handleWebPermission(request) }
                }
            }
        }
        setContentView(webView)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        val wanted = buildList {
            if (!hasMic()) add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
            ) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (wanted.isNotEmpty()) permissions.launch(wanted.toTypedArray())

        ReplayController.eventSink = { type, text -> postEvent(type, text) }
    }

    override fun onResume() {
        super.onResume()
        // Load (or reload after the backend URL changed in Device setup).
        val url = Prefs.backendUrl(this).trimEnd('/') + "/mobile/"
        if (url != loadedUrl) {
            loadedUrl = url
            webView.loadUrl(url)
        }
    }

    override fun onDestroy() {
        ReplayController.eventSink = null
        webView.destroy()
        super.onDestroy()
    }

    private fun hasMic() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** Only the backend's own page may use the microphone, and only the microphone. */
    private fun handleWebPermission(request: PermissionRequest) {
        val backend = Uri.parse(Prefs.backendUrl(this))
        val reqPort = if (request.origin.port != -1) request.origin.port else (if (request.origin.scheme == "https") 443 else 80)
        val backPort = if (backend.port != -1) backend.port else (if (backend.scheme == "https") 443 else 80)
        val sameOrigin = request.origin.scheme.equals(backend.scheme, ignoreCase = true) &&
            request.origin.host.equals(backend.host, ignoreCase = true) &&
            reqPort == backPort
        if (!sameOrigin || PermissionRequest.RESOURCE_AUDIO_CAPTURE !in request.resources) {
            request.deny()
            return
        }
        if (hasMic()) {
            request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
        } else {
            pendingMicRequest = request
            permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
        }
    }

    private fun postEvent(type: String, text: String) {
        val js = "window.onReplayEvent && window.onReplayEvent(${JSONObject().put("type", type).put("text", text)})"
        runOnUiThread { webView.evaluateJavascript(js, null) }
    }

    private fun showOfflinePage(reason: String) {
        val url = Prefs.backendUrl(this)
        val html = """
            <html><body style="background:#08090D;color:#eee;font-family:sans-serif;padding:24px">
            <h2>Can't reach the assistant server</h2>
            <p>${android.text.Html.escapeHtml(url)}<br><small>${android.text.Html.escapeHtml(reason)}</small></p>
            <p>Start the backend, or change its URL in Device setup.
            On USB: <code>adb reverse tcp:8000 tcp:8000</code> and use http://127.0.0.1:8000.</p>
            <button style="padding:12px 18px;font-size:16px" onclick="AndroidBridge.openSetup()">Device setup</button>
            <button style="padding:12px 18px;font-size:16px" onclick="location.href='${android.text.Html.escapeHtml(url.trimEnd('/'))}/mobile/'">Retry</button>
            </body></html>
        """.trimIndent()
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    /** Methods callable from the assistant's JavaScript (runs on a WebView binder thread). */
    inner class Bridge {
        private val audioRecorder = AudioRecorderHelper()

        @JavascriptInterface
        fun isNativeRecordingSupported(): Boolean = true

        @JavascriptInterface
        fun startAudioRecording(): Boolean {
            if (!hasMic()) {
                runOnUiThread { permissions.launch(arrayOf(Manifest.permission.RECORD_AUDIO)) }
                return false
            }
            return audioRecorder.start()
        }

        @JavascriptInterface
        fun stopAudioRecording(): String? {
            val base64 = audioRecorder.stop()
            if (base64 != null) {
                runOnUiThread {
                    webView.evaluateJavascript("window.onNativeAudioRecorded && window.onNativeAudioRecorded('$base64')", null)
                }
            }
            return base64
        }

        @JavascriptInterface
        fun startReplay(planJson: String): String {
            val plan = try {
                JSONObject(planJson)
            } catch (e: JSONException) {
                return "invalid plan"
            }
            ReplayController.preflight(this@MainActivity)?.let { return it }
            Log.i(TAG, "Starting replay for plan: ${plan.optString("summary")}")
            runOnUiThread { ReplayController.start(applicationContext, Prefs.backendUrl(this@MainActivity), plan) }
            return "started"
        }

        @JavascriptInterface
        fun stopReplay() {
            runOnUiThread { ReplayController.stop() }
        }

        @JavascriptInterface
        fun isReplayRunning(): Boolean = ReplayController.isRunning

        @JavascriptInterface
        fun openSetup() {
            runOnUiThread { startActivity(Intent(this@MainActivity, SetupActivity::class.java)) }
        }

        /** TEACH mode in the assistant UI. Demonstration capture is not built into this app yet. */
        @JavascriptInterface
        fun showFloatingOverlay(appName: String) {
            postEvent("failed", "Recording a demonstration isn't built into this app yet. Record it with " +
                "TapScreenRecorder (Show taps on), run the tap-to-flow pipeline, then POST the result to " +
                "/flows/teach — after that I can run \"$appName\" tasks for you.")
        }

        @JavascriptInterface
        fun hideFloatingOverlay() = Unit
    }
}
