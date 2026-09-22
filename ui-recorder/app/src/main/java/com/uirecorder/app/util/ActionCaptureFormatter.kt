package com.uirecorder.app.util

import com.uirecorder.app.data.ActionCaptureSession
import kotlinx.serialization.json.Json

object ActionCaptureFormatter {

    private val json = Json {
        prettyPrint = true
        encodeDefaults = true
    }

    fun toJson(session: ActionCaptureSession): String =
        json.encodeToString(ActionCaptureSession.serializer(), session)
}
