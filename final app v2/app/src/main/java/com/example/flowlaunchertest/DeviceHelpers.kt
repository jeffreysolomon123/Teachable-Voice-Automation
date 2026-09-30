package com.example.flowlaunchertest

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log

/** Finds the package of an installed app from the name the user said ("Zomato", "Amazon"). */
object AppResolver {
    private const val TAG = "AppResolver"

    fun packageFor(context: Context, appName: String?, explicitPackage: String? = null): String? {
        val pm = context.packageManager
        explicitPackage?.takeIf { it.isNotBlank() && pm.getLaunchIntentForPackage(it) != null }?.let { return it }
        val name = appName?.trim().orEmpty()
        if (name.isEmpty()) return null
        TARGET_APPS.firstOrNull { it.name.equals(name, ignoreCase = true) }?.pkg
            ?.takeIf { pm.getLaunchIntentForPackage(it) != null }?.let { return it }
        // Any launchable app whose label matches (needs the MAIN/LAUNCHER <queries> entry).
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(launcher, 0)
        val norm = { s: String -> s.lowercase().filter { it.isLetterOrDigit() } }
        val wanted = norm(name)
        val match = apps.firstOrNull { norm(it.loadLabel(pm).toString()) == wanted }
            ?: apps.firstOrNull { norm(it.loadLabel(pm).toString()).startsWith(wanted) }
        Log.i(TAG, "'$name' -> ${match?.activityInfo?.packageName}")
        return match?.activityInfo?.packageName
    }
}

/**
 * Developer option "Show taps": the tap-to-flow pipeline finds each tap by the white touch circle
 * in the video, so it must be on while a demonstration is recorded.
 *
 * The app can switch it itself only if granted WRITE_SECURE_SETTINGS once over USB:
 *   adb shell pm grant com.example.flowlaunchertest android.permission.WRITE_SECURE_SETTINGS
 * Otherwise the user turns it on in Settings > Developer options > Show taps.
 */
object ShowTaps {
    private const val TAG = "ShowTaps"
    private const val KEY = "show_touches"

    /** true/false when readable, null when this Android version hides the setting from apps. */
    fun isOn(context: Context): Boolean? =
        runCatching { Settings.System.getInt(context.contentResolver, KEY, 0) == 1 }
            .onFailure { Log.w(TAG, "Cannot read $KEY: ${it.message}") }.getOrNull()

    fun canChange(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    /** Turns Show taps on/off if permitted; returns whether it is now in the wanted state. */
    fun set(context: Context, on: Boolean): Boolean {
        if (!canChange(context)) return isOn(context) == on
        return runCatching { Settings.System.putInt(context.contentResolver, KEY, if (on) 1 else 0) }
            .onFailure { Log.w(TAG, "Cannot write $KEY: ${it.message}") }.getOrDefault(false)
    }

    fun openDeveloperOptions(context: Context) {
        runCatching {
            context.startActivity(Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
