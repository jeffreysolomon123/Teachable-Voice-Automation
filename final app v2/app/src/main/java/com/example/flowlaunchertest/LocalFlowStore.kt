package com.example.flowlaunchertest

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * Workflows learned by TEACH, kept on the phone: files/flows/<flow_id>.json holding
 *   {"flow": <semantic flow>, "grounded_flow": [...], "summary": {...}, "plan": {...}, "learned_at": ms}
 *
 * The phone is the source of truth for replay: before every automation run the stored flows are
 * pushed to the server (POST /flows?overwrite=true), so a restarted or different PC server can
 * still execute everything this phone has learned.
 */
object LocalFlowStore {
    private const val TAG = "LocalFlowStore"
    private val SAFE_ID = Regex("^[a-z0-9][a-z0-9_\\-]{0,63}$")

    private fun dir(context: Context) = File(context.filesDir, "flows").apply { mkdirs() }

    fun save(context: Context, flowId: String, record: JSONObject) {
        require(SAFE_ID.matches(flowId)) { "invalid flow id $flowId" }
        val target = File(dir(context), "$flowId.json")
        val tmp = File(dir(context), "$flowId.json.tmp")
        tmp.writeText(record.toString(2))
        if (!tmp.renameTo(target)) {
            target.delete()
            tmp.renameTo(target)
        }
        Log.i(TAG, "Saved learned flow $flowId (${target.length()} bytes)")
    }

    fun all(context: Context): List<JSONObject> =
        dir(context).listFiles { f -> f.name.endsWith(".json") }.orEmpty().sortedBy { it.name }.mapNotNull {
            runCatching { JSONObject(it.readText()) }.onFailure { e -> Log.w(TAG, "Unreadable ${it.name}", e) }.getOrNull()
        }

    fun delete(context: Context, flowId: String): Boolean =
        SAFE_ID.matches(flowId) && File(dir(context), "$flowId.json").delete()

    /** One line per learned flow, for the setup screen and the assistant UI. */
    fun describe(context: Context): List<String> = all(context).map { rec ->
        val flow = rec.optJSONObject("flow") ?: JSONObject()
        val steps = flow.optJSONArray("steps")?.length() ?: 0
        "${flow.optString("flow_id")} · ${flow.optString("app")} · $steps steps — ${flow.optString("description")}"
    }
}
