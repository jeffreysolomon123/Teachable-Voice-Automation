package com.uirecorder.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient

@Serializable
data class AccessibilityFingerprint(
    val resourceId: String? = null,
    val className: String? = null,
    val text: String? = null,
    val contentDescription: String? = null,
    val bounds: Bounds? = null
)

@Serializable
data class CapturedAction(
    // actionIndex exists only so the service can patch this record in place once an async
    // screenshot callback returns; it's excluded from the JSON the user actually inspects.
    @Transient val actionIndex: Int = -1,

    val actionType: String, // "tap" | "type" | "scroll" | "screen_transition"
    val timestampMs: Long,
    val packageName: String,

    // tap-specific
    val accessibilityFingerprint: AccessibilityFingerprint? = null,
    val screenshotPath: String? = null,
    val screenshotBlocked: Boolean = false,

    // type-specific
    val committedText: String? = null,

    // scroll-specific
    val scrollDirection: String? = null, // "up" | "down" | "unknown"
    val scrollDeltaUnreliable: Boolean = true
)

@Serializable
data class ActionCaptureSession(
    val startedAtMs: Long,
    val endedAtMs: Long,
    val actions: List<CapturedAction>
)
