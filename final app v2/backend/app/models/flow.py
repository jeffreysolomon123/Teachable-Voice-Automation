"""Semantic flow templates. Steps name targets by meaning; coordinates are not allowed."""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
FLOW_ID_RE = r"^[a-z0-9][a-z0-9_\-]{0,63}$"


def placeholders(text: str | None) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text or ""))


class StepAction(str, Enum):
    LAUNCH_APP = "LAUNCH_APP"
    TAP = "TAP"
    TYPE = "TYPE"
    SWIPE = "SWIPE"
    BACK = "BACK"
    WAIT = "WAIT"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


class Target(BaseModel):
    """What to tap, described visually/semantically. Resolved on each live screenshot."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=300)
    semantic_role: Optional[str] = Field(None, max_length=64)
    slot: Optional[str] = Field(None, max_length=64)
    # Visible label expected on the element (may contain {{slot}} placeholders).
    text: Optional[str] = Field(None, max_length=300)
    # Coarse hint only: "top", "middle", "bottom" third of the screen.
    region: Optional[Literal["top", "middle", "bottom"]] = None
    # Swipe up and look again when the target isn't on screen (lists, menus).
    scroll_search: bool = False


class Verify(BaseModel):
    """How to check that a step worked, on the next screenshot."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "anchors", "none"] = "auto"
    # Text expected on the next screen (fuzzy, may contain {{slot}}); used by "anchors".
    anchors: list[str] = Field(default_factory=list, max_length=10)


class FlowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1)
    action: StepAction
    app: Optional[str] = None
    target: Optional[Target] = None
    value: Optional[str] = Field(None, max_length=500)
    submit: bool = False
    direction: Optional[Literal["up", "down", "left", "right"]] = None
    duration_ms: Optional[int] = Field(None, ge=0, le=60_000)
    reason: Optional[str] = Field(None, max_length=300)
    verify: Verify = Field(default_factory=Verify)
    # Skip this step (instead of retrying) if its target is still absent after one recapture,
    # e.g. a customization sheet that only some items show.
    optional: bool = False
    # Force the payment/order gate for this step even if its text doesn't look like payment.
    final_confirmation: bool = False

    @model_validator(mode="after")
    def _action_fields(self) -> "FlowStep":
        a = self.action
        if a == StepAction.TAP and self.target is None:
            raise ValueError(f"step {self.step}: TAP needs a target")
        if a == StepAction.TYPE and not self.value:
            raise ValueError(f"step {self.step}: TYPE needs a value")
        if a == StepAction.SWIPE and self.direction is None:
            raise ValueError(f"step {self.step}: SWIPE needs a direction (up/down/left/right)")
        if a == StepAction.ASK_USER and not self.reason:
            raise ValueError(f"step {self.step}: ASK_USER needs a reason")
        return self

    def referenced_slots(self) -> set[str]:
        refs = placeholders(self.value) | placeholders(self.reason)
        if self.target:
            refs |= placeholders(self.target.description) | placeholders(self.target.text)
            if self.target.slot:
                refs.add(self.target.slot)
        for a in self.verify.anchors:
            refs |= placeholders(a)
        return refs


class Flow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str = Field(pattern=FLOW_ID_RE)
    app: str = Field(min_length=1, max_length=64)
    package: Optional[str] = Field(None, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    steps: list[FlowStep] = Field(min_length=1, max_length=200)
    source: Literal["manual", "teach"] = "manual"

    @field_validator("steps")
    @classmethod
    def _sequential(cls, steps: list[FlowStep]) -> list[FlowStep]:
        nums = [s.step for s in steps]
        if nums != list(range(1, len(steps) + 1)):
            raise ValueError(f"step numbers must be 1..{len(steps)} in order, got {nums}")
        return steps

    @model_validator(mode="after")
    def _slots_declared(self) -> "Flow":
        declared = set(self.required_slots) | set(self.optional_slots)
        overlap = set(self.required_slots) & set(self.optional_slots)
        if overlap:
            raise ValueError(f"slots both required and optional: {sorted(overlap)}")
        for s in self.steps:
            unknown = s.referenced_slots() - declared
            if unknown:
                raise ValueError(f"step {s.step} uses undeclared slots {sorted(unknown)}")
        return self


class FlowSummary(BaseModel):
    flow_id: str
    app: str
    description: str
    required_slots: list[str]
    optional_slots: list[str]
    steps: int
    source: str
