"""HTTP routes. Thin: validate input, call a service, return a typed model."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ..container import Container, get_container
from ..errors import ApiError, ErrorCode
from ..models.flow import Flow, FlowSummary
from ..models.replay import (ClientResult, ReplayConfirmRequest, ReplayResponse, ReplayStartRequest,
                             ReplayStopRequest)
from ..models.segment import MatchRequest, MatchResult, Screen, SegmentResponse
from ..models.slots import FlowMatchResponse, SlotPayload, SlotValidationResponse
from ..models.teach import TeachRequest, TeachResponse
from ..services import flow_service
from ..services.images import read_screenshot
from ..services.llm_service import SlotsOut
from ..services.teach_service import teach

router = APIRouter()


# ----------------------------------------------------------------------------- health

@router.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/dependencies", tags=["health"])
def health_dependencies(c: Container = Depends(get_container)) -> dict:
    s = c.settings
    return {
        "api": "ok",
        "segmentation": f"{c.segmentation.provider.name}: {c.segmentation.provider.status()}",
        "llm": "configured" if s.text_llm_enabled else "not_configured (GROQ_API_KEY + TEXT_MODEL)",
        "vision_llm": "configured" if s.vision_llm_enabled else "not_configured (GROQ_API_KEY + VISION_MODEL)",
        "flows": len(c.flows.list()),
        "voice_assistant": "mounted" if c.voice is not None else "disabled",
    }


# ----------------------------------------------------------------------------- slots

@router.post("/slots/validate", response_model=SlotValidationResponse, tags=["slots"])
def validate_slots(payload: SlotPayload, c: Container = Depends(get_container)) -> SlotValidationResponse:
    flow = c.flows.get(payload.flow_id) if payload.flow_id else None
    return flow_service.validate_slots(payload, flow, c.settings)


class ParseSlotsRequest(BaseModel):
    raw_query: str = Field(min_length=1, max_length=1000)


@router.post("/slots/parse", response_model=SlotsOut, tags=["slots"])
async def parse_slots(body: ParseSlotsRequest, c: Container = Depends(get_container)) -> SlotsOut:
    if not c.settings.text_llm_enabled:
        raise ApiError(ErrorCode.LLM_FAILED, "text LLM is not configured", status=503)
    out = await c.llm.parse_slots(body.raw_query)
    if out is None:
        raise ApiError(ErrorCode.LLM_FAILED, "could not parse slots", status=502, retryable=True)
    return out


# ----------------------------------------------------------------------------- flows

@router.post("/flows", response_model=Flow, status_code=201, tags=["flows"])
def create_flow(flow: Flow, overwrite: bool = False, c: Container = Depends(get_container)) -> Flow:
    if c.flows.exists(flow.flow_id) and not overwrite:
        raise ApiError(ErrorCode.FLOW_EXISTS, f"flow '{flow.flow_id}' exists (use ?overwrite=true)", status=409)
    c.flows.save(flow)
    c.flow_saved(flow)
    return flow


@router.get("/flows", response_model=list[FlowSummary], tags=["flows"])
def list_flows(c: Container = Depends(get_container)) -> list[FlowSummary]:
    return [FlowSummary(flow_id=f.flow_id, app=f.app, description=f.description, required_slots=f.required_slots,
                        optional_slots=f.optional_slots, steps=len(f.steps), source=f.source) for f in c.flows.list()]


@router.post("/flows/match", response_model=FlowMatchResponse, tags=["flows"])
async def match_flow(payload: SlotPayload, c: Container = Depends(get_container)) -> FlowMatchResponse:
    return await flow_service.match_flow(payload, c.flows, c.llm, c.settings)


@router.post("/flows/teach", response_model=TeachResponse, tags=["flows"])
async def teach_flow(req: TeachRequest, c: Container = Depends(get_container)) -> TeachResponse:
    if req.save and c.flows.exists(req.flow_id) and not req.overwrite:
        raise ApiError(ErrorCode.FLOW_EXISTS, f"flow '{req.flow_id}' exists (set overwrite)", status=409)
    res = await teach(req, c.llm)
    if req.save:
        c.flows.save(res.flow)
        c.flow_saved(res.flow)
        res.saved = True
    return res


@router.get("/flows/{flow_id}", response_model=Flow, tags=["flows"])
def get_flow(flow_id: str, c: Container = Depends(get_container)) -> Flow:
    return flow_service.require_flow(c.flows, flow_id)


# ----------------------------------------------------------------------------- vision

@router.post("/segment", response_model=SegmentResponse, tags=["vision"])
async def segment(file: UploadFile = File(...), c: Container = Depends(get_container)) -> SegmentResponse:
    shot = await read_screenshot(file, c.settings)
    screen = await c.segmentation.analyze(shot)
    return SegmentResponse(width=screen.width, height=screen.height, elements=screen.elements,
                           latency_ms=screen.latency_ms, provider=screen.provider)


@router.post("/match", response_model=MatchResult, tags=["vision"])
async def match(req: MatchRequest, c: Container = Depends(get_container)) -> MatchResult:
    if [e.index for e in req.elements] != list(range(len(req.elements))):
        raise ApiError(ErrorCode.INVALID_REQUEST, "element indexes must be 0..n-1 in order", status=422)
    w = req.width or max((e.bbox[2] for e in req.elements), default=1)
    h = req.height or max((e.bbox[3] for e in req.elements), default=1)
    screen = Screen(width=max(1, w), height=max(1, h), elements=req.elements)
    res = await c.resolver.resolve(req.target, screen, req.slots, None, allow_llm=req.allow_llm)
    return res.result


# ----------------------------------------------------------------------------- replay

@router.post("/replay/start", response_model=ReplayResponse, tags=["replay"])
async def replay_start(req: ReplayStartRequest, c: Container = Depends(get_container)) -> ReplayResponse:
    return await c.replay.start(req)


@router.post("/replay/step", response_model=ReplayResponse, tags=["replay"])
async def replay_step(session_id: str = Form(...), last_action_result: ClientResult = Form(ClientResult.OK),
                      screenshot: Optional[UploadFile] = File(None), file: Optional[UploadFile] = File(None),
                      c: Container = Depends(get_container)) -> ReplayResponse:
    shot = await read_screenshot(screenshot or file, c.settings)
    return await c.replay.step(session_id, shot, last_action_result)


@router.post("/replay/confirm", response_model=ReplayResponse, tags=["replay"])
async def replay_confirm(req: ReplayConfirmRequest, c: Container = Depends(get_container)) -> ReplayResponse:
    return await c.replay.confirm(req)


@router.post("/replay/stop", response_model=ReplayResponse, tags=["replay"])
async def replay_stop(req: ReplayStopRequest, c: Container = Depends(get_container)) -> ReplayResponse:
    return await c.replay.stop(req.session_id)
