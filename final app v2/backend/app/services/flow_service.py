"""Slot validation, slot substitution and flow matching."""
from __future__ import annotations

import re
from typing import Optional

from ..config import Settings
from ..errors import ApiError, ErrorCode
from ..models.flow import PLACEHOLDER_RE, Flow, FlowStep, placeholders
from ..models.slots import (FlowMatchResponse, SemanticTask, SlotPayload, SlotScalar, SlotState,
                            SlotValidationResponse)
from ..storage.flow_store import FlowStore
from .llm_service import LLMService
from .matching_service import norm

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def is_empty(v: SlotScalar) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def clean_value(v: SlotScalar, max_len: int) -> str:
    """Slot value as plain text: no control characters, collapsed whitespace, bounded length."""
    if isinstance(v, bool):
        text = "yes" if v else "no"
    elif isinstance(v, float) and v.is_integer():
        text = str(int(v))
    else:
        text = str(v)
    text = " ".join(_CONTROL.sub(" ", text).split())
    return text[:max_len]


def substitute(template: str | None, values: dict[str, str]) -> str | None:
    """Replace {{name}} once, left to right. Values are inserted literally, so a value containing
    "{{x}}" is never expanded again. Unknown names are an error (flows are validated up front)."""
    if template is None:
        return None

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(name)
        return values[name]

    return PLACEHOLDER_RE.sub(repl, template)


def slot_text_values(flow: Flow, slots: dict[str, SlotScalar], max_len: int) -> dict[str, str]:
    names = set(flow.required_slots) | set(flow.optional_slots)
    return {n: ("" if is_empty(slots.get(n)) else clean_value(slots.get(n), max_len)) for n in names}


def materialize_step(step: FlowStep, flow: Flow, values: dict[str, str]) -> Optional[FlowStep]:
    """Step with slots substituted, or None when it uses an optional slot that has no value."""
    empty_optional = {n for n in flow.optional_slots if not values.get(n)}
    if step.referenced_slots() & empty_optional:
        return None
    data = step.model_copy(deep=True)
    data.value = substitute(step.value, values)
    data.reason = substitute(step.reason, values)
    data.app = substitute(step.app, values)
    if data.target:
        data.target.description = substitute(step.target.description, values)
        data.target.text = substitute(step.target.text, values)
    data.verify.anchors = [substitute(a, values) for a in step.verify.anchors]
    return data


def missing_required(flow: Flow, slots: dict[str, SlotScalar]) -> list[str]:
    return [n for n in flow.required_slots if is_empty(slots.get(n))]


# ----------------------------------------------------------------------------- slot validation

def semantic_task(p: SlotPayload) -> SemanticTask:
    app = p.app_name or (p.slots.get("app") if isinstance(p.slots.get("app"), str) else None)
    variables = {k: v for k, v in p.slots.items() if k != "app"}
    return SemanticTask(goal=p.summary or (p.metadata.raw_query or ""), app=app, variables=variables)


def validate_slots(p: SlotPayload, flow: Optional[Flow], settings: Settings) -> SlotValidationResponse:
    problems: list[str] = []
    missing: list[str] = []
    for name in p.missing_slots:
        if name not in missing:
            missing.append(name)
    for name, st in p.slot_status.items():
        if st.status == SlotState.MISSING and name not in missing:
            missing.append(name)
        if name in p.slots and st.value != p.slots[name] and not (is_empty(st.value) and is_empty(p.slots[name])):
            problems.append(f"slot_status.{name}.value disagrees with slots.{name}")
    if flow:
        for name in missing_required(flow, p.slots):
            if name not in missing:
                missing.append(name)
    low = [n for n, st in p.slot_status.items()
           if st.status == SlotState.LOW_CONFIDENCE or st.confidence < settings.slot_min_confidence]
    for name, v in p.slots.items():
        if isinstance(v, str) and len(v) > settings.max_slot_value_length:
            problems.append(f"slots.{name} is longer than {settings.max_slot_value_length} characters")
    if not p.slots:
        problems.append("no slots given")
    requires_confirmation = bool(p.metadata.requires_user_confirmation and not p.confirmed) or bool(low)
    return SlotValidationResponse(
        valid=not missing and not problems, missing_slots=missing, low_confidence_slots=low,
        requires_confirmation=requires_confirmation, flow_id=flow.flow_id if flow else None,
        task=semantic_task(p), problems=problems)


# ----------------------------------------------------------------------------- flow matching

def _tokens(text: str | None) -> set[str]:
    stop = {"a", "an", "the", "on", "from", "in", "to", "of", "using", "with", "for", "me", "how", "teach", "my"}
    return {t for t in norm(text).replace("_", " ").split() if t not in stop}


def score_flow(flow: Flow, p: SlotPayload) -> float:
    """0..1: app match gates; then required-slot coverage and wording overlap."""
    app = p.app_name or (p.slots.get("app") if isinstance(p.slots.get("app"), str) else None)
    if app and norm(app) != norm(flow.app):
        return 0.0
    if p.flow_id and p.flow_id == flow.flow_id:
        return 1.0
    provided = {k for k, v in p.slots.items() if not is_empty(v)}
    req = set(flow.required_slots)
    coverage = len(req & provided) / len(req) if req else 0.5
    declared = req | set(flow.optional_slots)
    slot_fit = len(provided & declared) / len(provided) if provided else 0.0
    words = _tokens(p.summary) | _tokens(p.metadata.raw_query) | _tokens(p.flow_id)
    flow_words = _tokens(flow.description) | _tokens(flow.flow_id)
    overlap = len(words & flow_words) / len(flow_words) if flow_words else 0.0
    return round(min(1.0, 0.25 + 0.35 * coverage + 0.2 * slot_fit + 0.2 * overlap), 3)


async def match_flow(p: SlotPayload, store: FlowStore, llm: LLMService, settings: Settings) -> FlowMatchResponse:
    flows = store.list()
    scored = sorted(((score_flow(f, p), f) for f in flows), key=lambda t: -t[0])
    scored = [t for t in scored if t[0] > 0]
    cands = [{"flow_id": f.flow_id, "score": s} for s, f in scored[:5]]
    if not scored:
        return FlowMatchResponse(found=False, method="deterministic", candidates=cands)
    best_s, best = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    if best_s >= 0.75 and best_s - runner >= 0.1:
        return FlowMatchResponse(found=True, flow_id=best.flow_id, confidence=best_s, method="deterministic",
                                 missing_slots=missing_required(best, p.slots), candidates=cands)
    if settings.text_llm_enabled:
        summary = p.summary or p.metadata.raw_query or ""
        out = await llm.match_flow(summary, p.app_name, [
            {"flow_id": f.flow_id, "app": f.app, "description": f.description} for _, f in scored[:10]])
        if out and out.flow_id and out.confidence >= settings.llm_confidence_threshold:
            chosen = store.get(out.flow_id)
            if chosen:
                return FlowMatchResponse(found=True, flow_id=chosen.flow_id, confidence=out.confidence,
                                         method="text_llm", missing_slots=missing_required(chosen, p.slots),
                                         candidates=cands)
    if best_s >= 0.6 and best_s - runner >= 0.1:
        return FlowMatchResponse(found=True, flow_id=best.flow_id, confidence=best_s, method="deterministic",
                                 missing_slots=missing_required(best, p.slots), candidates=cands)
    return FlowMatchResponse(found=False, confidence=best_s, method="deterministic", candidates=cands)


def require_flow(store: FlowStore, flow_id: str) -> Flow:
    flow = store.get(flow_id)
    if flow is None:
        raise ApiError(ErrorCode.FLOW_NOT_FOUND, f"flow '{flow_id}' not found", status=404)
    return flow


__all__ = ["substitute", "materialize_step", "missing_required", "validate_slots", "match_flow",
           "slot_text_values", "clean_value", "placeholders", "require_flow"]
