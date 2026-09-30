"""Tiered target resolution: deterministic -> text LLM -> vision LLM.

Coordinates always come from a visual source: the chosen element's bounding-box center
(tiers 0-1) or the vision model's point on the current screenshot (tier 2), validated against
the screenshot size.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

from ..config import Settings
from ..models.flow import Target
from ..models.segment import MatchResult, Screen
from .images import Screenshot
from .llm_service import LLMService
from .matching_service import match_deterministic, point_in_screen

log = logging.getLogger("app.resolver")


class Tier(IntEnum):
    DETERMINISTIC = 0
    TEXT_LLM = 1
    VISION_LLM = 2


@dataclass
class Resolution:
    result: MatchResult
    llm_calls: int = 0


def _region(y: int, height: int) -> str:
    return "top" if y < height / 3 else "middle" if y < 2 * height / 3 else "bottom"


class Resolver:
    def __init__(self, settings: Settings, llm: LLMService):
        self.s, self.llm = settings, llm

    def deterministic(self, target: Target, screen: Screen, slots: dict[str, object],
                      exclude: frozenset[int] = frozenset()) -> MatchResult:
        s = self.s
        elements = [e for e in screen.elements if e.index not in exclude] if exclude else screen.elements
        return match_deterministic(target, elements, slots, width=screen.width, height=screen.height,
                                   confident=s.match_confident_threshold, margin=s.match_ambiguity_margin,
                                   min_candidate=s.match_min_candidate_score, max_candidates=s.llm_max_candidates)

    async def resolve(self, target: Target, screen: Screen, slots: dict[str, object],
                      shot: Optional[Screenshot] = None, *, min_tier: Tier = Tier.DETERMINISTIC,
                      allow_llm: bool = True, exclude: frozenset[int] = frozenset()) -> Resolution:
        """``exclude``: element indexes that must not be chosen (e.g. the text field echoing
        what was just typed)."""
        calls_before = self.llm.calls
        det = self.deterministic(target, screen, slots, exclude)
        if det.found and min_tier == Tier.DETERMINISTIC:
            return Resolution(det)
        if not allow_llm:
            return Resolution(det)

        # Tier 1: text LLM chooses among OCR/segmentation candidates (never invents coordinates).
        if min_tier <= Tier.TEXT_LLM and self.s.text_llm_enabled:
            cands = [c for c in self._candidates(det, screen) if c["index"] not in exclude]
            if cands:
                out = await self.llm.disambiguate_element(self._describe(target, slots), cands)
                if out and out.selected_index is not None and out.confidence >= self.s.llm_confidence_threshold:
                    el = screen.elements[out.selected_index]
                    x, y = el.center
                    return Resolution(MatchResult(
                        found=True, index=el.index, confidence=round(out.confidence, 3), method="text_llm",
                        x=x, y=y, candidates=det.candidates,
                        reason=f"text LLM chose #{el.index} '{el.text[:40]}'"), self.llm.calls - calls_before)

        # Tier 2: vision LLM looks at the screenshot itself, with the detected boxes as marks.
        if shot is not None and self.s.vision_llm_enabled:
            jpeg, scale = shot.as_jpeg(80, self.s.vision_image_width)
            sw, sh = round(shot.width * scale), round(shot.height * scale)
            marks = [{"index": e.index, "type": e.type, "text": e.text, **({"label": e.label} if e.label else {}),
                      "bbox": [round(v * scale) for v in e.bbox]}
                     for e in screen.elements if e.index not in exclude][: self.s.vision_max_marks]
            out = await self.llm.vision_resolve_element(jpeg, sw, sh, self._describe(target, slots), marks)
            if out and out.found and out.confidence >= self.s.vision_confidence_threshold:
                if out.index is not None:
                    el = screen.elements[out.index]
                    x, y = el.center
                    return Resolution(MatchResult(
                        found=True, index=el.index, confidence=round(out.confidence, 3), method="vision_llm",
                        x=x, y=y, candidates=det.candidates, reason=f"vision LLM chose box #{el.index}"),
                        self.llm.calls - calls_before)
                x, y = int(out.x / scale), int(out.y / scale)
                if point_in_screen(x, y, screen.width, screen.height):
                    return Resolution(MatchResult(
                        found=True, index=self._element_at(screen, x, y), confidence=round(out.confidence, 3),
                        method="vision_llm", x=x, y=y, candidates=det.candidates,
                        reason="vision LLM located the target (free point)"), self.llm.calls - calls_before)
                log.warning("vision point (%d, %d) outside %dx%d; ignored", x, y, screen.width, screen.height)

        det.reason = det.reason or "target not found"
        return Resolution(det, self.llm.calls - calls_before)

    def _candidates(self, det: MatchResult, screen: Screen) -> list[dict]:
        """Deterministic candidates if any, else every text element (bounded)."""
        idxs = [c["index"] for c in det.candidates] or [e.index for e in screen.elements if e.text or e.label]
        out = []
        for i in idxs[: self.s.llm_max_candidates]:
            e = screen.elements[i]
            out.append({"index": e.index, "text": e.text, "type": e.type, **({"label": e.label} if e.label else {}),
                        "region": _region(e.center[1], screen.height)})
        return out

    @staticmethod
    def _describe(target: Target, slots: dict[str, object]) -> str:
        parts = [target.description]
        if target.text:
            parts.append(f"visible label: {target.text}")
        if target.semantic_role:
            parts.append(f"role: {target.semantic_role}")
        if target.slot and slots.get(target.slot) not in (None, ""):
            parts.append(f"{target.slot} = {slots[target.slot]}")
        return "; ".join(parts)

    @staticmethod
    def _element_at(screen: Screen, x: int, y: int) -> Optional[int]:
        hits = [e for e in screen.elements if e.bbox[0] <= x <= e.bbox[2] and e.bbox[1] <= y <= e.bbox[3]]
        if not hits:
            return None
        return min(hits, key=lambda e: (e.bbox[2] - e.bbox[0]) * (e.bbox[3] - e.bbox[1])).index
