import asyncio
import io
import time
import uuid
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse
from pydantic import BaseModel
from voice_assistant_app.config import BASE_DIR, STATIC_DIR
from voice_assistant_app.turn_manager import TurnManager
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionMemory, SessionManager

app = FastAPI(title="Teachable Voice Assistant Server", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory = WorkflowMemory()
session_manager = SessionManager()
session_memory = session_manager.get_session("default")
turn_manager = TurnManager(
    memory=memory,
    session_memory=session_memory,
    session_manager=session_manager
)

STATIC_DIR.mkdir(exist_ok=True, parents=True)
APK_FILE = BASE_DIR / "build" / "TeachableVoiceAssistant.apk"

class TextTurnRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    session_id: Optional[str] = None

class SessionRequest(BaseModel):
    session_id: Optional[str] = None

class EventTriggerRequest(BaseModel):
    trigger_type: str
    details: str = ""
    session_id: Optional[str] = None

def _get_sid(session_id: Optional[str] = None, x_session_id: Optional[str] = None) -> str:
    return (session_id or x_session_id or "default").strip() or "default"

@app.get("/api/voice/health")
async def health_check():
    """Health check endpoint for mobile app auto-discovery and connectivity verification."""
    return {
        "status": "ok",
        "service": "TeachableVoiceAssistant",
        "voice": "Ava",
        "timestamp": time.time()
    }

@app.post("/api/voice/session/new")
async def create_new_session(req: Optional[SessionRequest] = None):
    """Create a fresh session or reset an existing session with clean state."""
    sid = req.session_id if (req and req.session_id) else f"sess_{uuid.uuid4().hex[:8]}"
    sess = session_manager.reset_session(sid)
    return {
        "session_id": sid,
        "status": "ready",
        "message": "Fresh session initialized with zero prior memory."
    }

@app.post("/api/voice/session/cancel")
async def cancel_session_workflow(req: Optional[SessionRequest] = None, x_session_id: Optional[str] = Header(None)):
    """Cancel in-progress workflow, clearing draft plan and pending clarifications."""
    sid = _get_sid(req.session_id if req else None, x_session_id)
    session_manager.cancel_session_workflow(sid)
    return {
        "session_id": sid,
        "status": "cancelled",
        "message": "Active workflow cancelled. Memory reset for next action."
    }

@app.get("/api/voice/state")
async def get_state(session_id: Optional[str] = None, x_session_id: Optional[str] = Header(None)):
    sid = _get_sid(session_id, x_session_id)
    return turn_manager.get_state(session_id=sid)

@app.get("/api/voice/workflows")
async def get_workflows(session_id: Optional[str] = None, x_session_id: Optional[str] = Header(None)):
    sid = _get_sid(session_id, x_session_id)
    sess = session_manager.get_session(sid)
    return {
        "session_id": sid,
        "workflows": memory.get_all_workflows(),
        "last_run": memory.get_last_run_status(),
        "preferences": sess.get_all_preferences(),
        "history": sess.get_recent_history(limit=10)
    }

@app.delete("/api/voice/history")
async def reset_history(session_id: Optional[str] = None, x_session_id: Optional[str] = Header(None)):
    sid = _get_sid(session_id, x_session_id)
    session_manager.reset_session(sid)
    return {"session_id": sid, "status": "cleared", "message": f"Conversation history for session {sid} reset."}

@app.post("/api/voice/process-text")
async def process_text(req: TextTurnRequest, x_session_id: Optional[str] = Header(None)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    sid = _get_sid(req.session_id, x_session_id)
    result = await turn_manager.process_user_text(
        req.text,
        voice_override=req.voice,
        session_id=sid
    )
    return result

@app.post("/api/voice/process-audio")
async def process_audio(
    file: UploadFile = File(...),
    voice: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    x_session_id: Optional[str] = Header(None)
):
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    filename = file.filename or "voice_input.webm"
    sid = _get_sid(session_id, x_session_id)
    result = await turn_manager.process_user_audio(
        audio_bytes,
        filename=filename,
        voice_override=voice,
        session_id=sid
    )
    return result

@app.post("/api/voice/transcribe")
async def transcribe_only(file: UploadFile = File(...)):
    """Speech-to-text only (no orchestrator, no TTS): used by the app's scripted demo mode."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    text = await asyncio.to_thread(turn_manager.stt.transcribe_audio_bytes, audio_bytes,
                                   file.filename or "voice_input.webm")
    return {"text": text or ""}

@app.post("/api/voice/trigger-event")
async def trigger_event(req: EventTriggerRequest):
    result = turn_manager.trigger_execution_event(req.trigger_type, details=req.details)
    return result

@app.get("/api/voice/tts")
async def get_tts_audio(text: str, voice: Optional[str] = None):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    audio_bytes = await turn_manager.tts.synthesize_to_bytes(text, voice=voice)
    return Response(content=audio_bytes, media_type="audio/mpeg")

@app.api_route("/download/voice-assistant.apk", methods=["GET", "HEAD"])
@app.api_route("/download/TeachableVoiceAssistant.apk", methods=["GET", "HEAD"])
async def download_apk():
    """Direct downloadable APK for Android devices."""
    candidates = [
        BASE_DIR / "build" / "TeachableVoiceAssistant.apk",
        BASE_DIR.parent / "TeachableVoiceAssistant.apk",
        BASE_DIR.parent / "voice_assistant_app" / "build" / "TeachableVoiceAssistant.apk"
    ]
    for p in candidates:
        if p.exists():
            return FileResponse(
                p,
                media_type="application/vnd.android.package-archive",
                filename="TeachableVoiceAssistant.apk"
            )
    raise HTTPException(status_code=404, detail="APK not found on server.")

# Serve Frontend static assets
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Frontend not found, please check static files."}
