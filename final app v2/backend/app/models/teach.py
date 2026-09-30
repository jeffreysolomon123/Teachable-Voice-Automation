"""TEACH: turn a demonstrated recording (grounded_flow.json) into a semantic flow template."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .flow import FLOW_ID_RE, Flow
from .slots import SlotScalar


class TeachRequest(BaseModel):
    flow_id: str = Field(pattern=FLOW_ID_RE)
    app: str
    package: Optional[str] = None
    description: str
    # Slot values used during the demonstration, e.g. {"restaurant": "Pizza Hut", "item": "chicken"}.
    slots: dict[str, SlotScalar]
    required_slots: Optional[list[str]] = None
    optional_slots: list[str] = Field(default_factory=list)
    # Output of tap-to-flow-pipeline stage 4 (grounded_flow.json): a list of step objects.
    grounded_steps: list[dict[str, Any]]
    # Inclusive step_index range of the recording that belongs to the task (drops e.g. opening the
    # app from the launcher and stopping the recorder).
    first_step_index: Optional[int] = None
    last_step_index: Optional[int] = None
    use_llm: bool = True
    save: bool = False
    overwrite: bool = False


class TeachResponse(BaseModel):
    flow: Flow
    warnings: list[str]
    llm_used: bool
    saved: bool
