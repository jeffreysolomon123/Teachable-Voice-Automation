package com.uirecorder.app.data

import kotlinx.serialization.Serializable

@Serializable
data class Bounds(
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int
)

@Serializable
data class NodeInfo(
    val resourceId: String? = null,
    val className: String? = null,
    val text: String? = null,
    val contentDescription: String? = null,
    val hintText: String? = null,
    val bounds: Bounds? = null,
    val isClickable: Boolean = false,
    val isCheckable: Boolean = false,
    val isChecked: Boolean = false,
    val isEnabled: Boolean = false,
    val isFocused: Boolean = false,
    val isScrollable: Boolean = false,
    val isEditable: Boolean = false,
    val isPassword: Boolean = false,
    val isLongClickable: Boolean = false,
    val childCount: Int? = null,
    val depth: Int? = null,
    val ancestors: List<NodeInfo>? = null
)

@Serializable
data class UiEvent(
    val eventType: String,
    val packageName: String,
    val timestampWallClockMs: Long,
    val timestampMonotonicMs: Long,
    val eventText: List<String> = emptyList(),
    val scrollDeltaX: Int? = null,
    val scrollDeltaY: Int? = null,
    val scrollX: Int? = null,
    val scrollY: Int? = null,
    val beforeText: String? = null,
    val addedCount: Int? = null,
    val removedCount: Int? = null,
    val source: NodeInfo? = null,
    val fullTreeSnapshot: List<NodeInfo>? = null
)

@Serializable
data class RecordingSession(
    val startedAtMs: Long,
    val endedAtMs: Long,
    val events: List<UiEvent>
)
