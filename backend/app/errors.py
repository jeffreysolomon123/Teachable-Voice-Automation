"""One error shape for every API failure: {"error": {"code", "message", "retryable"}}."""
from __future__ import annotations

import logging
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_SLOTS = "INVALID_SLOTS"
    MISSING_SLOT = "MISSING_SLOT"
    FLOW_NOT_FOUND = "FLOW_NOT_FOUND"
    FLOW_INVALID = "FLOW_INVALID"
    FLOW_EXISTS = "FLOW_EXISTS"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_STATE_INVALID = "SESSION_STATE_INVALID"
    SCREEN_INVALID = "SCREEN_INVALID"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    SEGMENTATION_FAILED = "SEGMENTATION_FAILED"
    LLM_FAILED = "LLM_FAILED"
    VISION_FAILED = "VISION_FAILED"
    BLOCKER_DETECTED = "BLOCKER_DETECTED"
    USER_CONFIRMATION_REQUIRED = "USER_CONFIRMATION_REQUIRED"
    CONFIRMATION_STALE = "CONFIRMATION_STALE"
    REPLAY_TIMEOUT = "REPLAY_TIMEOUT"
    REPLAY_FAILED = "REPLAY_FAILED"
    INPUT_NOT_SUPPORTED = "INPUT_NOT_SUPPORTED"
    ANDROID_CAPABILITY_UNAVAILABLE = "ANDROID_CAPABILITY_UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


def error_body(code: ErrorCode | str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": str(getattr(code, "value", code)), "message": message, "retryable": retryable}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, exc.retryable), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        code = ErrorCode.INVALID_SLOTS if request.url.path.startswith("/slots") else ErrorCode.INVALID_REQUEST
        parts = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        return JSONResponse(error_body(code, "; ".join(parts) or "invalid request"), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_REQUEST
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(error_body(ErrorCode.INTERNAL_ERROR, "internal server error", True), status_code=500)
