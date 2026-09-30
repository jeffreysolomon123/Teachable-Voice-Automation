"""Minimal OpenRouter client (OpenAI-compatible HTTP) for the voice assistant.

Used when OPENROUTER_API_KEY is set: chat (JSON) for the orchestrator, and speech-to-text by
sending the recorded audio to a Gemini model as an input_audio part. Standard library only.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from voice_assistant_app.config import OPENROUTER_API_KEY, OPENROUTER_CHAT_MODEL, OPENROUTER_STT_MODEL

URL = "https://openrouter.ai/api/v1/chat/completions"
# Skip Google's slow, queued "flex" tier.
PROVIDER = {"ignore": ["google-ai-studio/flex"]}


def enabled() -> bool:
    return bool(OPENROUTER_API_KEY)


def _post(body: Dict[str, Any], timeout: float) -> Optional[str]:
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    choice = (resp.get("choices") or [{}])[0]
    if resp.get("error") or choice.get("finish_reason") == "error":
        raise RuntimeError(f"OpenRouter error: {resp.get('error') or 'finish_reason=error'}")
    return (choice.get("message") or {}).get("content")


def chat_json(messages: List[Dict[str, str]], model: Optional[str] = None, attempts: int = 3) -> Optional[str]:
    """One chat completion in JSON mode; returns the raw content string or None."""
    body = {"model": model or OPENROUTER_CHAT_MODEL, "messages": messages, "temperature": 0.1,
            "max_tokens": 1500, "response_format": {"type": "json_object"}, "provider": PROVIDER}
    for attempt in range(1, attempts + 1):
        try:
            content = _post(body, timeout=30)
            if content:
                return content
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as e:
            print(f"[OpenRouter] chat attempt {attempt} failed: {e}")
    return None


def transcribe(audio_bytes: bytes, audio_format: str, hint: str, attempts: int = 3) -> str:
    """Speech-to-text: verbatim English transcription of the audio clip ("" on failure)."""
    prompt = ("Transcribe the spoken audio verbatim in English. Return ONLY the transcription text, "
              "no quotes, timestamps or notes. If there is no speech, return an empty string. "
              f"Names that may occur: {hint}")
    body = {
        "model": OPENROUTER_STT_MODEL, "temperature": 0, "max_tokens": 300, "provider": PROVIDER,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio_bytes).decode(),
                                                    "format": audio_format}},
            {"type": "text", "text": prompt},
        ]}],
    }
    for attempt in range(1, attempts + 1):
        try:
            content = _post(body, timeout=45)
            if content is not None:
                return content.strip().strip('"').strip("'")
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as e:
            print(f"[OpenRouter] transcription attempt {attempt} failed: {e}")
    return ""
