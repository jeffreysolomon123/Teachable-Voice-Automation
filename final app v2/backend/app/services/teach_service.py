"""grounded_flow.json -> semantic Flow, using only visual evidence.

Used per tap: the vision-grounded element (segmentation box + OCR text) and the OCR text of the
before/after frames. Ignored: the tap log's accessibility ``element`` (text/resourceId/className/
bounds), ``log_bounds`` picks (their vision ``inner_element`` is used instead), and the raw tap
coordinates, which never reach the template.

Known limitation: ``type_text`` values are reconstructed by the pipeline from the tap log's
keyboard events; they are used as the typed value and then generalized into {{slot}}s.
"""
from __future__ import annotations

from typing import Any, Optional

from ..errors import ApiError, ErrorCode
from ..models.flow import Flow, FlowStep, StepAction, Target, Verify, placeholders
from ..models.teach import TeachRequest, TeachResponse
from .flow_service import clean_value, is_empty
from .llm_service import LLMService
from .matching_service import norm, text_score
from .safety_service import final_action_phrase


def _visual_element(step: dict[str, Any]) -> Optional[dict[str, Any]]:
    g = step.get("grounded_element")
    if not isinstance(g, dict):
        return None
    if g.get("grounded_confidence") == "log_bounds":
        return g.get("inner_element")  # the vision pick, not the accessibility bounds
    return g


def _region(el: dict[str, Any], height: Optional[float]) -> Optional[str]:
    if not height or not el.get("bbox"):
        return None
    cy = (el["bbox"][1] + el["bbox"][3]) / 2
    return "top" if cy < height / 3 else "middle" if cy < 2 * height / 3 else "bottom"


def _slot_for(text: str, slot_values: dict[str, str], *, prefix_ok: bool = False) -> Optional[str]:
    t = norm(text)
    if not t:
        return None
    best, best_s = None, 0.0
    for name, value in slot_values.items():
        v = norm(value)
        if not v:
            continue
        s = text_score(v, t)
        if prefix_ok and len(t) >= 3 and v.startswith(t):
            s = max(s, 0.85)
        if s > best_s:
            best, best_s = name, s
    return best if best_s >= 0.8 else None


def _heuristic_role(text: str, region: Optional[str]) -> Optional[str]:
    t = norm(text)
    if not t:
        return None
    if final_action_phrase(text):
        return "place_order"
    if t.startswith("search") or "restaurant name" in t:
        return "search_field"
    if t.startswith("add item") or t.startswith("add to cart"):
        return "add_item_confirm"
    if t == "add" or t.startswith("add "):
        return "add_item"
    if t.startswith("see all") or t.startswith("view all"):
        return "see_all"
    if "view cart" in t or t.startswith("checkout"):
        return "cart"
    if t.endswith("continue") or t.startswith("continue") or t.startswith("next"):
        return "continue"
    return None


async def teach(req: TeachRequest, llm: LLMService) -> TeachResponse:
    slot_values = {k: clean_value(v, 200) for k, v in req.slots.items() if not is_empty(v)}
    warnings: list[str] = []
    steps_in = [s for s in req.grounded_steps if isinstance(s, dict) and s.get("type") in ("tap", "type_text")]
    if req.first_step_index is not None:
        steps_in = [s for s in steps_in if s.get("step_index", 0) >= req.first_step_index]
    if req.last_step_index is not None:
        steps_in = [s for s in steps_in if s.get("step_index", 0) <= req.last_step_index]
    if not steps_in:
        raise ApiError(ErrorCode.INVALID_REQUEST, "no tap/type_text steps in the selected range", status=422)

    # Frame height is not in grounded_flow.json; the largest box edge is a safe lower bound.
    height = max((max(_visual_element(s)["bbox"][3], 1) for s in steps_in
                  if s.get("type") == "tap" and _visual_element(s) and _visual_element(s).get("bbox")), default=None)

    out: list[dict[str, Any]] = [{"action": StepAction.LAUNCH_APP, "app": req.app}]
    llm_inputs: list[dict[str, Any]] = []
    tap_positions: list[int] = []
    last_slot: Optional[str] = None
    for s in steps_in:
        idx = s.get("step_index")
        if s["type"] == "type_text":
            typed = str(s.get("text") or "")
            slot = _slot_for(typed, slot_values, prefix_ok=True)
            if slot:
                out.append({"action": StepAction.TYPE, "value": f"{{{{{slot}}}}}"})
                last_slot = slot
            elif typed:
                warnings.append(f"step {idx}: typed '{typed}' matches no slot; kept as literal text")
                out.append({"action": StepAction.TYPE, "value": typed})
            continue

        el = _visual_element(s) or {}
        text = " ".join(str(el.get("text") or "").split())
        label = " ".join(str(el.get("label") or "").split())
        region = _region(el, height)
        slot = _slot_for(text, slot_values)
        role = _heuristic_role(text, region)
        target: dict[str, Any] = {"region": region}
        if role == "search_field":
            # Search hints rotate ("Search 'pizza'", "Search 'biryani'"): match by role, not label.
            target.update(description="search field", semantic_role="search_field")
        elif slot:
            target.update(description=f"{slot.replace('_', ' ')} matching {{{{{slot}}}}}",
                          semantic_role=f"{slot}_result", slot=slot, text=f"{{{{{slot}}}}}")
            last_slot = slot
        elif role == "add_item":
            target.update(description="Add button" + (f" for {{{{{last_slot}}}}}" if last_slot else ""),
                          semantic_role="add_item", slot=last_slot, text="ADD")
        elif role == "add_item_confirm":
            # Customization sheets appear only for some items, so the step may be skipped.
            target.update(description="Add item button on the item customization sheet", semantic_role=role,
                          text="Add item")
        elif role:
            label = {"continue": "Continue", "see_all": "See all", "cart": "View cart"}.get(role)
            target.update(description=f"{role.replace('_', ' ')} button ('{text[:40]}')", semantic_role=role,
                          text=label)
        elif text:
            target.update(description=f"element labelled '{text[:60]}'", text=text[:80])
        elif label:
            # Text-less icon/image described by the segmentation model ("back arrow", "pizza photo").
            target.update(description=f"{label[:80]} in the {region or 'screen'} area")
        else:
            target.update(description=f"unlabelled icon in the {region or 'screen'} area")
            warnings.append(f"step {idx}: tapped element has no text; replay will need the vision tier")
        step: dict[str, Any] = {"action": StepAction.TAP, "target": target}
        if role == "add_item_confirm":
            step["optional"] = True
        if final_action_phrase(text):
            step["final_confirmation"] = True
        after = s.get("after_text") or []
        if slot and any(text_score(norm(slot_values[slot]), norm(a)) >= 0.8 for a in after):
            step["verify"] = {"mode": "anchors", "anchors": [f"{{{{{slot}}}}}"]}
        out.append(step)
        tap_positions.append(len(out) - 1)
        llm_inputs.append({"tapped_text": text or None, "tapped_visual_label": label or None, "region": region,
                           "current_description": target["description"],
                           "before_text": (s.get("before_text") or [])[:15],
                           "after_text": (after or [])[:15]})

    llm_used = False
    if req.use_llm and llm.s.text_llm_enabled and llm_inputs:
        described = await llm.describe_teach_steps(llm_inputs)
        if described:
            llm_used = True
            for pos, d in zip(tap_positions, described.steps):
                t = out[pos]["target"]
                allowed = placeholders(t["description"]) | ({t["slot"]} if t.get("slot") else set())
                if placeholders(d.description) <= allowed:  # the LLM may not invent slots
                    t["description"] = d.description
                if d.semantic_role and not t.get("semantic_role"):
                    t["semantic_role"] = d.semantic_role
        else:
            warnings.append("LLM step description failed; kept the deterministic descriptions")

    used = set()
    steps: list[FlowStep] = []
    for i, st in enumerate(out, start=1):
        target = Target(**{k: v for k, v in st["target"].items() if v is not None}) if "target" in st else None
        step = FlowStep(step=i, action=st["action"], app=st.get("app"), value=st.get("value"), target=target,
                        verify=Verify(**st.get("verify", {})), final_confirmation=st.get("final_confirmation", False),
                        optional=st.get("optional", False))
        used |= step.referenced_slots()
        steps.append(step)
    required = req.required_slots if req.required_slots is not None else sorted(used - set(req.optional_slots))
    optional = sorted((set(req.optional_slots) | (set(slot_values) - set(required))) - {"app"})
    try:
        flow = Flow(flow_id=req.flow_id, app=req.app, package=req.package, description=req.description,
                    required_slots=required, optional_slots=[o for o in optional if o not in required],
                    steps=steps, source="teach")
    except ValueError as exc:
        raise ApiError(ErrorCode.FLOW_INVALID, f"generated flow is invalid: {exc}", status=422) from exc
    return TeachResponse(flow=flow, warnings=warnings, llm_used=llm_used, saved=False)
