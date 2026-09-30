package com.example.flowlaunchertest

import android.content.Context

object Prefs {
    private const val FILE = "flow_launcher_prefs"
    private const val KEY_BACKEND = "backend_url"
    private const val KEY_SLOTS = "slot_json"
    private const val KEY_OLD_GROQ = "groq_api_key" // removed feature: the key now lives on the backend

    // The demo server on the PC, reached over Wi-Fi (change it in Device setup if the PC's IP changes).
    // Over USB instead: `adb reverse tcp:8000 tcp:8000` and use http://127.0.0.1:8000.
    const val DEFAULT_BACKEND_URL = "http://192.168.1.10:8000"

    private fun prefs(context: Context) = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun backendUrl(context: Context): String =
        prefs(context).getString(KEY_BACKEND, DEFAULT_BACKEND_URL).orEmpty().ifBlank { DEFAULT_BACKEND_URL }

    fun setBackendUrl(context: Context, url: String) {
        prefs(context).edit().putString(KEY_BACKEND, url.trim()).apply()
    }

    fun slotJson(context: Context): String? = prefs(context).getString(KEY_SLOTS, null)

    fun setSlotJson(context: Context, json: String) {
        prefs(context).edit().putString(KEY_SLOTS, json).apply()
    }

    /** Deletes a Groq key stored by older versions of this app. */
    fun clearLegacySecrets(context: Context) {
        prefs(context).edit().remove(KEY_OLD_GROQ).apply()
    }
}
