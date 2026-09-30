from __future__ import annotations

import os
import tempfile

# Must run before app.main is imported: its module-level app mounts the voice assistant, whose
# memory would otherwise be written into the repository (assistant/voice_assistant_app/data).
os.environ["VOICE_DATA_DIR"] = tempfile.mkdtemp(prefix="voice_data_")

import io
import json
import shutil
from pathlib import Path
from typing import Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.services.llm_service import LLMService
from app.services.segmentation.base import RawSegmentation, SegmentationError

# app.main loads backend/.env into os.environ; tests must never use a developer's real keys.
for _key in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_key, None)

W, H = 1080, 2400
FLOWS_SRC = Path(__file__).resolve().parents[1] / "flows"


def el(text: str, bbox, *, type_: str = "text", source: str = "ocr", id_: int | None = None) -> dict:
    return {"id": id_, "text": text, "bbox": list(bbox), "type": type_, "source": source, "confidence": 0.9,
            "parent": None}


class FakeProvider:
    """Returns queued screens (combined-element lists); repeats the last one when the queue runs dry."""

    name = "fake"

    def __init__(self):
        self.queue: list[list[dict] | Exception] = []
        self.last: list[dict] = []
        self.calls = 0

    def push(self, *screens):
        self.queue.extend(screens)

    def status(self) -> str:
        return "configured"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        self.calls += 1
        item = self.queue.pop(0) if self.queue else self.last
        if isinstance(item, Exception):
            raise item
        self.last = item
        els = [dict(e, id=e["id"] if e.get("id") is not None else i) for i, e in enumerate(item)]
        return RawSegmentation(width, height, els)


class FakeLLM:
    """httpx MockTransport answering Groq chat completions from a queue of reply strings."""

    def __init__(self):
        self.replies: list[str | int] = []
        self.requests: list[dict] = []

    def push(self, *replies):
        self.replies.extend(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0) if self.replies else '{"selected_index": null, "confidence": 0}'
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"message": "boom"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})


def png_bytes(w: int = W, h: int = H, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 240, 240)).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def flows_dir(tmp_path) -> Path:
    d = tmp_path / "flows"
    shutil.copytree(FLOWS_SRC, d)
    return d


@pytest.fixture
def make_settings(flows_dir) -> Callable[..., Settings]:
    def _make(**kw) -> Settings:
        base = dict(app_env="test", flows_dir=str(flows_dir), groq_api_key="", text_model="", vision_model="",
                    screen_settle_delay_ms=500, launch_settle_delay_ms=1500, _env_file=None)
        base.update(kw)
        return Settings(**base)
    return _make


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def make_client(make_settings, provider, fake_llm):
    def _make(llm: bool = False, **kw) -> TestClient:
        if llm:
            kw.setdefault("groq_api_key", "test-key")
            kw.setdefault("text_model", "text-model")
            kw.setdefault("vision_model", "vision-model")
        settings = make_settings(**kw)
        service = LLMService(settings, transport=httpx.MockTransport(fake_llm.handler))
        return TestClient(create_app(settings, provider=provider, llm=service, voice=False))
    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def upload(png: bytes | None = None, name="screen.png", ctype="image/png"):
    return {"screenshot": (name, png if png is not None else png_bytes(), ctype)}


__all__ = ["el", "FakeProvider", "FakeLLM", "png_bytes", "upload", "SegmentationError", "W", "H"]
