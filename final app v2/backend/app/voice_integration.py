"""Hosts the voice assistant (assistant/voice_assistant_app) inside this backend.

One service then serves:
  /api/voice/*   the assistant's STT -> orchestrator -> TTS API (its own code, unchanged)
  /mobile/       the assistant's mobile UI, loaded by the Android app's WebView (same origin,
                 so no CORS and microphone access works on https / 127.0.0.1)
  /, /static     the assistant's desktop web UI

The assistant decides WHAT the user wants (a confirmed plan = Slot JSON). The Android app hands
that plan to /flows/match + /replay/* here, which decide HOW on screen. To keep the two in sync,
every stored semantic flow is registered in the assistant's workflow memory, so the assistant
knows which workflows can actually be executed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models.flow import Flow
from .storage.flow_store import FlowStore

log = logging.getLogger("app.voice")


class VoiceBridge:
    """Handle to the imported assistant: its workflow memory, for syncing flows."""

    def __init__(self, memory):
        self.memory = memory

    def register_flow(self, flow: Flow) -> None:
        # Trigger phrases help the assistant's matcher; slots stay empty (they come from speech).
        phrases = [flow.description, flow.flow_id.replace("_", " ")]
        self.memory.save_workflow(flow.flow_id, flow.app, phrases, {}, description=flow.description)

    def record_run(self, flow_id: str, status: str, step_stopped: str, details: str) -> None:
        """Replay outcome -> the assistant's "last run" memory (answers "did my order go through?")."""
        self.memory.record_run_result(flow_id=flow_id, status=status, step_stopped=step_stopped, details=details)

    def sync(self, store: FlowStore) -> int:
        known = self.memory.get_all_workflows()
        added = 0
        for flow in store.list():
            if flow.flow_id not in known:
                self.register_flow(flow)
                added += 1
        return added


def mount_voice_assistant(app: FastAPI, settings: Settings, store: FlowStore) -> Optional[VoiceBridge]:
    if not settings.voice_assistant_enabled:
        return None
    root = Path(settings.voice_assistant_dir).resolve()
    if not (root / "voice_assistant_app" / "server.py").is_file():
        log.warning("voice assistant not found in %s; /api/voice is disabled", root)
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from voice_assistant_app import server as voice_server  # noqa: WPS433 (optional component)
    except Exception as exc:  # missing deps (groq, edge-tts, ...) must not take the backend down
        log.warning("voice assistant failed to load (%s: %s); /api/voice is disabled", type(exc).__name__, exc)
        return None

    for route in voice_server.app.router.routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            app.router.routes.append(route)
    static_dir = Path(voice_server.STATIC_DIR)
    mobile_dir = root / "mobile_assistant_app" / "assets"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="voice_static")
    if mobile_dir.is_dir():
        app.mount("/mobile", StaticFiles(directory=mobile_dir, html=True), name="voice_mobile")

    bridge = VoiceBridge(voice_server.memory)
    added = bridge.sync(store)
    log.info("voice assistant mounted from %s (registered %d semantic flows)", root, added)
    return bridge
