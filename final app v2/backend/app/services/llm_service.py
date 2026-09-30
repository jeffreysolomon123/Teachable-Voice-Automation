"""Groq (OpenAI-compatible) text + vision calls. Every reply is parsed into a typed schema.

The API key never leaves the backend and is never logged. Callers get ``None`` back (and a
warning in the log) when the model is not configured or its output is malformed/invalid, so a
bad LLM answer can never turn into an action.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Optional, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import Settings, get_settings
from ..models.slots import SlotScalar

log = logging.getLogger("app.llm")
T = TypeVar("T", bound=BaseModel)


# ----------------------------------------------------------------------------- output schemas

class DisambiguationOut(BaseModel):
    selected_index: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0)


class VisionOut(BaseModel):
    found: bool
    index: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class FlowMatchOut(BaseModel):
    flow_id: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)


class ReadinessOut(BaseModel):
    ready: bool
    blocker: Optional[str] = Field(None, max_length=200)
    needs_user: bool = False


class SlotsOut(BaseModel):
    app_name: Optional[str] = None
    summary: Optional[str] = None
    slots: dict[str, SlotScalar] = Field(default_factory=dict)


class TeachStepOut(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    semantic_role: Optional[str] = Field(None, max_length=64)


class TeachOut(BaseModel):
    steps: list[TeachStepOut]


def parse_json_reply(raw: str) -> Any:
    """Strip <think> blocks and code fences, then parse the first JSON object in the reply."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw or "", flags=re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


class LLMService:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self._transport = transport  # tests inject a mock transport
        self.calls = 0

    # ------------------------------------------------------------------------- plumbing

    async def _chat(self, model: str, messages: list[dict], *, max_tokens: int, json_mode: bool) -> Optional[str]:
        if not (self.s.groq_api_key and model):
            return None
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        effort = self.s.text_reasoning_effort if model == self.s.text_model else self.s.vision_reasoning_effort
        if effort:
            body["reasoning_effort"] = effort
        self.calls += 1
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self.s.llm_timeout_seconds) as client:
                r = await client.post(f"{self.s.groq_base_url}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {self.s.groq_api_key}"})
        except httpx.HTTPError as exc:
            log.warning("llm call failed model=%s error=%s", model, type(exc).__name__)
            return None
        latency = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            msg = ""
            try:
                msg = str(r.json().get("error", {}).get("message", ""))[:200]
            except ValueError:
                pass
            log.warning("llm call model=%s http=%d latency_ms=%d %s", model, r.status_code, latency, msg)
            return None
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("llm call model=%s returned an unexpected body", model)
            return None
        log.info("llm call model=%s latency_ms=%d", model, latency)
        return content

    async def _ask(self, schema: type[T], model: str, messages: list[dict], *, max_tokens: int = 300,
                   json_mode: bool = True) -> Optional[T]:
        raw = await self._chat(model, messages, max_tokens=max_tokens, json_mode=json_mode)
        if raw is None:
            return None
        try:
            return schema.model_validate(parse_json_reply(raw))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            log.warning("llm reply rejected schema=%s error=%s", schema.__name__, str(exc)[:200])
            return None

    @staticmethod
    def _image_part(jpeg: bytes) -> dict:
        return {"type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}}

    # ------------------------------------------------------------------------- tasks

    async def disambiguate_element(self, target_description: str, candidates: list[dict]) -> Optional[DisambiguationOut]:
        """Pick one of ``candidates`` (dicts with index/text/type/region). Never returns coordinates."""
        lines = "\n".join(f"{c['index']}: {c.get('text') or '(no text)'}  [type={c.get('type')}, "
                          f"region={c.get('region', '?')}]" for c in candidates)
        prompt = (
            "You select UI elements on a mobile app screen from OCR/segmentation output.\n"
            f"TARGET:\n{target_description}\n\nELEMENTS (index: text [attributes]):\n{lines}\n\n"
            "Choose the single element that best matches the TARGET. If none matches, use null.\n"
            'Reply with ONLY JSON: {"selected_index": <index or null>, "confidence": <0..1>}'
        )
        out = await self._ask(DisambiguationOut, self.s.text_model, [{"role": "user", "content": prompt}], max_tokens=120)
        if out and out.selected_index is not None and out.selected_index not in {c["index"] for c in candidates}:
            log.warning("llm selected index %s which is not a candidate; rejected", out.selected_index)
            return DisambiguationOut(selected_index=None, confidence=0.0)
        return out

    async def vision_resolve_element(self, jpeg: bytes, width: int, height: int, target_description: str,
                                     candidates: list[dict] | None = None) -> Optional[VisionOut]:
        """Locate the target on the screenshot.

        Set-of-marks: the model sees the detected element boxes (pixel coordinates of the supplied,
        possibly scaled, image) and should answer with an index, so the final point comes from a
        segmentation box. Free x/y (same pixel space) is accepted only as a validated fallback:
        in testing, models were far less accurate at raw coordinates than at picking a box.
        """
        cands = candidates or []
        listing = "\n".join(f"{c['index']}: [{','.join(str(v) for v in c['bbox'])}] {c.get('type', '')} "
                            f"{(c.get('text') or '')[:40]!r}" for c in cands)
        prompt = (
            f"This is a screenshot of a mobile app, exactly {width}x{height} pixels.\n"
            f"Find this UI element: {target_description}.\n"
            + (f"Detected UI elements (index: pixel box [x1,y1,x2,y2], type, text):\n{listing}\n"
               "If one of these boxes is the element, answer with its index. " if cands else "")
            + "If no box fits but the element is visible, give its center as pixel x/y in THIS image. "
              'Reply with ONLY JSON: {"found": true|false, "index": <int or null>, "x": <int or null>, '
              '"y": <int or null>, "confidence": <0..1>}'
        )
        msg = [{"role": "user", "content": [{"type": "text", "text": prompt}, self._image_part(jpeg)]}]
        out = await self._ask(VisionOut, self.s.vision_model, msg, max_tokens=150)
        if not (out and out.found):
            return out
        if out.index is not None:
            if out.index in {c["index"] for c in cands}:
                return out
            log.warning("vision picked index %s which is not a candidate; rejected", out.index)
            return VisionOut(found=False, confidence=0.0)
        if out.x is None or out.y is None or not (0 <= out.x < width and 0 <= out.y < height):
            log.warning("vision coordinates (%s, %s) outside %dx%d; rejected", out.x, out.y, width, height)
            return VisionOut(found=False, confidence=0.0)
        return out

    async def match_flow(self, request_summary: str, app: str | None, flows: list[dict]) -> Optional[FlowMatchOut]:
        listing = "\n".join(f"- {f['flow_id']} (app: {f['app']}): {f['description']}" for f in flows)
        prompt = (
            f"USER REQUEST: {request_summary}\nAPP: {app or 'unknown'}\n\nSTORED FLOWS:\n{listing}\n\n"
            "Which stored flow performs this request (flows are generic; slot values differ)? "
            'Reply with ONLY JSON: {"flow_id": "<id or null>", "confidence": <0..1>}'
        )
        out = await self._ask(FlowMatchOut, self.s.text_model, [{"role": "user", "content": prompt}], max_tokens=100)
        if out and out.flow_id is not None and out.flow_id not in {f["flow_id"] for f in flows}:
            return FlowMatchOut(flow_id=None, confidence=0.0)
        return out

    async def screen_readiness(self, jpeg: bytes, width: int, height: int) -> Optional[ReadinessOut]:
        prompt = (
            f"You are looking at a screenshot of a mobile app, exactly {width}x{height} pixels. Is it the app's "
            "normal usable screen, or is something blocking it (splash screen, permission dialog, login prompt, "
            "update prompt, popup ad, error, loading spinner)? Set needs_user=true if a human must act (login, "
            'payment, choice). Reply with ONLY JSON: {"ready": true|false, "blocker": "<short phrase or null>", '
            '"needs_user": true|false}'
        )
        msg = [{"role": "user", "content": [{"type": "text", "text": prompt}, self._image_part(jpeg)]}]
        return await self._ask(ReadinessOut, self.s.vision_model, msg, max_tokens=120)

    async def parse_slots(self, raw_query: str) -> Optional[SlotsOut]:
        prompt = (
            "Extract WHAT the user wants from this request as slots (not how to do it on screen). "
            "Use lowercase snake_case slot names such as item, restaurant, app, quantity, address, "
            f"customizations.\nREQUEST: {raw_query}\n"
            'Reply with ONLY JSON: {"app_name": <string or null>, "summary": <string>, "slots": {<name>: <value>}}'
        )
        return await self._ask(SlotsOut, self.s.text_model, [{"role": "user", "content": prompt}], max_tokens=300)

    async def describe_teach_steps(self, steps: list[dict]) -> Optional[TeachOut]:
        """Name each taught tap semantically (description + role) from its visual context."""
        prompt = (
            "A user demonstrated a task in a mobile app. For each tap below you get the tapped element's "
            "text (from OCR) or, for icons/images without text, a visual label, its screen region, and text "
            "visible before/after. Describe each tapped target "
            "generically so it can be found again on a different day, and give a snake_case semantic_role "
            "(e.g. search_field, restaurant_result, food_item, add_item, cart, continue). Keep any "
            "{{slot}} placeholders exactly as written.\n\n"
            + json.dumps(steps, ensure_ascii=False)
            + '\n\nReply with ONLY JSON: {"steps": [{"description": "...", "semantic_role": "..."}, ...]} '
              "with exactly one entry per tap, in order."
        )
        out = await self._ask(TeachOut, self.s.text_model, [{"role": "user", "content": prompt}], max_tokens=1500)
        if out and len(out.steps) != len(steps):
            log.warning("teach description count %d != %d taps; rejected", len(out.steps), len(steps))
            return None
        return out


def get_llm_service() -> LLMService:
    return LLMService(get_settings())
