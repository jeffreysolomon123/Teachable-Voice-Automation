"""Slot JSON: WHAT the user wants (never HOW to do it on screen)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

SlotScalar = Union[str, int, float, bool, None]


class SlotState(str, Enum):
    FILLED = "FILLED"
    DEFAULT = "DEFAULT"
    USER_PREFERENCE = "USER_PREFERENCE"
    INFERRED = "INFERRED"
    MISSING = "MISSING"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class SlotStatus(BaseModel):
    value: SlotScalar = None
    status: SlotState
    confidence: float = Field(ge=0.0, le=1.0)


class SlotMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    raw_query: Optional[str] = None
    requires_user_confirmation: bool = False


class SlotPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: Optional[str] = None
    stage: Optional[str] = None
    status: Optional[str] = None
    flow_id: Optional[str] = None
    app_name: Optional[str] = None
    summary: Optional[str] = None
    confirmed: bool = False
    slots: dict[str, SlotScalar] = Field(default_factory=dict)
    slot_status: dict[str, SlotStatus] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    metadata: SlotMetadata = Field(default_factory=SlotMetadata)


class SemanticTask(BaseModel):
    goal: str
    app: Optional[str]
    variables: dict[str, SlotScalar]


class SlotValidationResponse(BaseModel):
    valid: bool
    missing_slots: list[str]
    low_confidence_slots: list[str]
    requires_confirmation: bool
    flow_id: Optional[str] = None
    task: SemanticTask
    problems: list[str] = Field(default_factory=list)


class FlowMatchResponse(BaseModel):
    found: bool
    flow_id: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    method: str
    missing_slots: list[str] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
