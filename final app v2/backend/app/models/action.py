"""Actions sent to Android. Coordinates are pixels of the screenshot the client uploaded."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ActionType(str, Enum):
    LAUNCH_APP = "LAUNCH_APP"
    TAP = "TAP"
    TYPE = "TYPE"
    SWIPE = "SWIPE"
    BACK = "BACK"
    WAIT = "WAIT"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


class Action(BaseModel):
    action: ActionType
    # LAUNCH_APP
    app: Optional[str] = None
    package: Optional[str] = None
    # TAP
    x: Optional[int] = Field(None, ge=0)
    y: Optional[int] = Field(None, ge=0)
    # SWIPE
    x1: Optional[int] = Field(None, ge=0)
    y1: Optional[int] = Field(None, ge=0)
    x2: Optional[int] = Field(None, ge=0)
    y2: Optional[int] = Field(None, ge=0)
    duration_ms: Optional[int] = Field(None, ge=0, le=60_000)
    # TYPE
    text: Optional[str] = None
    submit: bool = False
    # Size of the screenshot the coordinates refer to (client scales to its display).
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    # ASK_USER: echo this id to /replay/confirm so the answer can't approve something else.
    confirmation_id: Optional[str] = None
    kind: Optional[str] = None
    # How long the client should wait after executing before the next screenshot.
    settle_ms: Optional[int] = Field(None, ge=0)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    method: Optional[str] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _fields(self) -> "Action":
        a = self.action
        if a == ActionType.TAP and (self.x is None or self.y is None):
            raise ValueError("TAP needs x and y")
        if a == ActionType.SWIPE and None in (self.x1, self.y1, self.x2, self.y2):
            raise ValueError("SWIPE needs x1, y1, x2, y2")
        if a == ActionType.TYPE and self.text is None:
            raise ValueError("TYPE needs text")
        if a == ActionType.LAUNCH_APP and not (self.app or self.package):
            raise ValueError("LAUNCH_APP needs app or package")
        if a == ActionType.ASK_USER and not self.reason:
            raise ValueError("ASK_USER needs a reason")
        for name, bound in (("x", self.screen_width), ("x1", self.screen_width), ("x2", self.screen_width),
                            ("y", self.screen_height), ("y1", self.screen_height), ("y2", self.screen_height)):
            v = getattr(self, name)
            if v is not None and bound is not None and v >= bound:
                raise ValueError(f"{name}={v} is outside the {bound}px screenshot")
        return self
