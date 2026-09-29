package com.example.flowlaunchertest

import android.content.Context

object Prefs {
    private const val FILE = "flow_launcher_prefs"
    private const val KEY_GROQ = "groq_api_key"

    fun groqApiKey(context: Context): String =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_GROQ, "").orEmpty()

    fun setGroqApiKey(context: Context, key: String) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY_GROQ, key.trim()).apply()
    }
}
