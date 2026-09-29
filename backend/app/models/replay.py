"""Replay session state and the replay API contract."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .action import Action
from .slots import SlotScalar


class ReplayState(str, Enum):
    IDLE = "IDLE"
    LOADING_FLOW = "LOADING_FLOW"
    LAUNCHING_APP = "LAUNCHING_APP"
    WAITING_FOR_SCREEN = "WAITING_FOR_SCREEN"
    ANALYZING_SCREEN = "ANALYZING_SCREEN"
    RESOLVING_TARGET = "RESOLVING_TARGET"
    EXECUTING_ACTION = "EXECUTING_ACTION"
    VERIFYING = "VERIFYING"
    NEXT_STEP = "NEXT_STEP"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    RETRYING = "RETRYING"
    ASKING_USER = "ASKING_USER"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


TERMINAL_STATES = {ReplayState.COMPLETED, ReplayState.FAILED, ReplayState.STOPPED}


class ReplayStartRequest(BaseModel):
    flow_id: str
    slots: dict[str, SlotScalar] = Field(default_factory=dict)
    session_id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class ReplayResponse(BaseModel):
    session_id: str
    flow_id: str
    step: int
    total_steps: int
    state: ReplayState
    status: Literal["running", "continue", "waiting_for_user", "completed", "failed", "stopped"]
    action: Optional[Action] = None
    # Same object as ``action`` (the /replay/start contract calls it "next_action").
    next_action: Optional[Action] = None
    message: Optional[str] = None
    llm_calls: int = 0


class ReplayConfirmRequest(BaseModel):
    session_id: str
    confirmed: bool
    confirmation_id: Optional[str] = None


class ReplayStopRequest(BaseModel):
    session_id: str


class ClientResult(str, Enum):
    """What the Android client reports about the action it last executed."""

    OK = "ok"
    INPUT_NOT_SUPPORTED = "input_not_supported"
    GESTURE_FAILED = "gesture_failed"
    APP_NOT_INSTALLED = "app_not_installed"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
