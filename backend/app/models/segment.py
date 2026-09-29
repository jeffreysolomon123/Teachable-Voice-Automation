"""Visual elements produced by segmentation + OCR, and element matching I/O."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .flow import Target


class Element(BaseModel):
    index: int = Field(ge=0)
    text: str = ""
    type: str
    bbox: tuple[int, int, int, int]
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: Literal["cv", "ocr"] = "cv"
    parent: Optional[int] = None

    @field_validator("bbox")
    @classmethod
    def _ordered(cls, b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = b
        if x2 < x1 or y2 < y1 or min(b) < 0:
            raise ValueError(f"invalid bbox {b}")
        return b

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) // 2, (y1 + y2) // 2


class Screen(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    elements: list[Element]
    latency_ms: int = 0
    provider: str = ""

    @property
    def all_text(self) -> list[str]:
        return [e.text for e in self.elements if e.text]


class SegmentResponse(BaseModel):
    width: int
    height: int
    elements: list[Element]
    latency_ms: int
    provider: str


class MatchRequest(BaseModel):
    target: Target
    elements: list[Element]
    slots: dict[str, object] = Field(default_factory=dict)
    # Screen size is optional; used for region hints.
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    allow_llm: bool = True


class MatchResult(BaseModel):
    found: bool
    index: Optional[int] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    method: Literal["deterministic", "text_llm", "vision_llm", "none"]
    ambiguous: bool = False
    x: Optional[int] = None
    y: Optional[int] = None
    candidates: list[dict] = Field(default_factory=list)
    reason: str = ""
