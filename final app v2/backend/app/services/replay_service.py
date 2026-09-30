"""Replay as an explicit state machine.

Request/response loop with the Android client:

    /replay/start          -> first action (usually LAUNCH_APP)
    client executes it, waits settle_ms, takes a screenshot
    /replay/step(shot)     -> ANALYZING_SCREEN -> VERIFYING (did the last action work?)
                              -> NEXT_STEP -> RESOLVING_TARGET -> EXECUTING_ACTION (next action)
    ... until COMPLETED, or ASK_USER (WAITING_FOR_CONFIRMATION / ASKING_USER) -> /replay/confirm

Every state change goes through ``ReplaySession.transition``, which rejects transitions that are
not in ``TRANSITIONS``. The payment/final-order gate lives here, in code, not in a prompt.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import Settings
from ..errors import ApiError, ErrorCode
from ..models.action import Action, ActionType
from ..models.flow import Flow, FlowStep, StepAction
from ..models.replay import (TERMINAL_STATES, ClientResult, ReplayConfirmRequest, ReplayResponse, ReplayStartRequest,
                             ReplayState as S)
from ..models.segment import Element, Screen
from ..storage.flow_store import FlowStore
from .flow_service import materialize_step, missing_required, require_flow, slot_text_values
from .images import Screenshot
from .llm_service import LLMService
from .matching_service import norm, text_score
from .resolver import Resolver, Tier
from .safety_service import Blocker, detect_blocker, gate_reason
from .segmentation import SegmentationService

log = logging.getLogger("app.replay")

TRANSITIONS: dict[S, set[S]] = {
    S.IDLE: {S.LOADING_FLOW},
    S.LOADING_FLOW: {S.NEXT_STEP, S.FAILED},
    S.NEXT_STEP: {S.LAUNCHING_APP, S.RESOLVING_TARGET, S.EXECUTING_ACTION, S.WAITING_FOR_SCREEN, S.ASKING_USER,
                  S.COMPLETED},
    S.LAUNCHING_APP: {S.EXECUTING_ACTION},
    S.EXECUTING_ACTION: {S.ANALYZING_SCREEN},
    S.WAITING_FOR_SCREEN: {S.ANALYZING_SCREEN},
    S.ANALYZING_SCREEN: {S.VERIFYING, S.NEXT_STEP, S.BLOCKED, S.RETRYING, S.ASKING_USER, S.FAILED},
    S.VERIFYING: {S.NEXT_STEP, S.RETRYING},
    S.RETRYING: {S.RESOLVING_TARGET, S.EXECUTING_ACTION, S.ASKING_USER},
    S.RESOLVING_TARGET: {S.EXECUTING_ACTION, S.WAITING_FOR_CONFIRMATION, S.RETRYING, S.BLOCKED, S.NEXT_STEP},
    S.BLOCKED: {S.EXECUTING_ACTION, S.ASKING_USER},
    S.WAITING_FOR_CONFIRMATION: {S.WAITING_FOR_SCREEN},
    S.ASKING_USER: {S.WAITING_FOR_SCREEN},
    S.COMPLETED: set(),
    S.FAILED: set(),
    S.STOPPED: set(),
}
# Any live session may be stopped or fail.
for _s in TRANSITIONS:
    if _s not in TERMINAL_STATES:
        TRANSITIONS[_s] |= {S.STOPPED, S.FAILED}


class InvalidTransition(Exception):
    pass


@dataclass
class Pending:
    """The action handed to the client, awaiting verification on the next screenshot."""

    kind: str  # "step" | "wait" | "scroll" | "dismiss"
    step_idx: int
    action: Action
    element: Optional[Element] = None
    prev_texts: list[str] = field(default_factory=list)


@dataclass
class Confirmation:
    id: str
    kind: str  # "payment_gate" | "blocker" | "retries_exhausted" | "flow_ask" | "input_fallback"
    step_idx: int
    reason: str
    created_at: float
    element_text: str = ""


@dataclass
class ReplaySession:
    session_id: str
    flow: Flow
    slots: dict
    values: dict[str, str]
    state: S = S.IDLE
    step_idx: int = 0
    pending: Optional[Pending] = None
    confirmation: Optional[Confirmation] = None
    # One-time approval of the gated action of a step (released only for the same label).
    approved_gate: Optional[tuple[int, str, float]] = None
    # Where the text typed by the last TYPE shows up (the input field): excluded from target
    # resolution until the next tap, so "Domino's" in the search box isn't mistaken for the result.
    typed_box: Optional[tuple[int, int, int, int]] = None
    typed_text: str = ""
    # Text the user typed by hand (input fallback); its echo is located on the next screen.
    echo_text: str = ""
    # A step whose verification was interrupted by a blocker dismissal; verified afterwards.
    interrupted: Optional[Pending] = None
    retries: int = 0
    scrolls: int = 0
    dismissals: int = 0
    llm_calls: int = 0
    last_action: Optional[Action] = None
    message: Optional[str] = None
    history: list[tuple[str, str, str]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def transition(self, to: S, why: str = "") -> None:
        if to not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state.value} -> {to.value} is not allowed")
        self.history.append((self.state.value, to.value, why))
        self.state = to
        self.updated_at = time.time()

    @property
    def step_no(self) -> int:
        return min(self.step_idx + 1, len(self.flow.steps))


class SessionStore:
    def __init__(self, settings: Settings):
        self.s = settings
        self._sessions: dict[str, ReplaySession] = {}

    def _evict(self) -> None:
        now = time.time()
        for sid in [k for k, v in self._sessions.items() if now - v.updated_at > self.s.session_ttl_seconds]:
            del self._sessions[sid]
        while len(self._sessions) >= self.s.max_sessions:
            oldest = min(self._sessions.values(), key=lambda v: (v.state not in TERMINAL_STATES, v.updated_at))
            del self._sessions[oldest.session_id]

    def add(self, sess: ReplaySession) -> None:
        self._evict()
        existing = self._sessions.get(sess.session_id)
        if existing and existing.state not in TERMINAL_STATES:
            raise ApiError(ErrorCode.SESSION_STATE_INVALID, f"session {sess.session_id} is already running; "
                           "stop it first", status=409)
        self._sessions[sess.session_id] = sess

    def get(self, session_id: str) -> ReplaySession:
        sess = self._sessions.get(session_id)
        if sess is None or time.time() - sess.updated_at > self.s.session_ttl_seconds:
            raise ApiError(ErrorCode.SESSION_NOT_FOUND, f"session '{session_id}' not found or expired", status=404)
        return sess


def screen_texts(screen: Screen) -> list[str]:
    return [norm(t) for t in screen.all_text]


def screen_changed(prev: list[str], screen: Screen, tapped: Optional[Element]) -> bool:
    """True when the visible text changed noticeably, or the tapped element's label vanished
    from where it was (e.g. "ADD" turned into "1 +")."""
    now = screen_texts(screen)
    a, b = set(prev), set(now)
    if a or b:
        jaccard = len(a & b) / max(1, len(a | b))
        if jaccard < 0.9:
            return True
    if tapped is not None and tapped.text:
        t = norm(tapped.text)
        tx, ty = tapped.center
        tol = max(40, (tapped.bbox[3] - tapped.bbox[1]))
        still_there = any(norm(e.text) == t and abs(e.center[0] - tx) <= tol and abs(e.center[1] - ty) <= tol
                          for e in screen.elements)
        return not still_there
    return False


def text_on_screen(text: str, screen: Screen, threshold: float = 0.8) -> bool:
    q = norm(text)
    return bool(q) and any(text_score(q, norm(e.text)) >= threshold for e in screen.elements if e.text)


# Replay outcome -> (status, step_stopped) recorded for the voice assistant's "last run" reports.
_REPORTS = {
    S.COMPLETED: "SUCCESS",
    S.FAILED: "FAILED",
    S.STOPPED: "STOPPED",
}
_ASK_REPORTS = {
    "payment_gate": "PAUSED_AT_PAYMENT",
    "retries_exhausted": "STUCK_IN_EXECUTION",
    "blocker": "BLOCKED",
    "input_fallback": "WAITING_FOR_USER_INPUT",
}

# reporter(flow_id, status, step_stopped, details)
RunReporter = Callable[[str, str, str, str], None]


class ReplayService:
    def __init__(self, settings: Settings, store: FlowStore, sessions: SessionStore, seg: SegmentationService,
                 llm: LLMService, reporter: Optional[RunReporter] = None):
        self.s, self.store, self.sessions, self.seg, self.llm = settings, store, sessions, seg, llm
        self.resolver = Resolver(settings, llm)
        self.reporter = reporter

    def _report(self, sess: ReplaySession, before: S) -> None:
        """Tell the reporter (voice assistant memory) when a run ends or needs the user."""
        if self.reporter is None or sess.state == before:
            return
        status = _REPORTS.get(sess.state)
        if status is None and sess.state in (S.WAITING_FOR_CONFIRMATION, S.ASKING_USER) and sess.confirmation:
            status = _ASK_REPORTS.get(sess.confirmation.kind)
        if status is None:
            return
        step = sess.flow.steps[min(sess.step_idx, len(sess.flow.steps) - 1)]
        where = f"step {sess.step_no}: " + (step.target.description if step.target else step.action.value)
        try:
            self.reporter(sess.flow.flow_id, status, where[:120], (sess.message or "")[:300])
        except Exception:
            log.warning("run reporter failed", exc_info=True)

    # ------------------------------------------------------------------------- API entry points

    async def start(self, req: ReplayStartRequest) -> ReplayResponse:
        flow = require_flow(self.store, req.flow_id)
        missing = missing_required(flow, req.slots)
        if missing:
            raise ApiError(ErrorCode.MISSING_SLOT, f"missing required slots: {', '.join(missing)}", status=422)
        for name, v in req.slots.items():
            if isinstance(v, str) and len(v) > self.s.max_slot_value_length:
                raise ApiError(ErrorCode.INVALID_SLOTS, f"slot '{name}' is too long", status=422)
        values = slot_text_values(flow, req.slots, self.s.max_slot_value_length)
        sid = req.session_id or f"sess_{secrets.token_hex(6)}"
        sess = ReplaySession(sid, flow, dict(req.slots), values)
        self.sessions.add(sess)
        async with sess.lock:
            sess.transition(S.LOADING_FLOW, "start")
            sess.transition(S.NEXT_STEP, "flow loaded")
            action = await self._advance(sess, screen=None, shot=None)
            self._report(sess, S.NEXT_STEP)
            return self._response(sess, action, status="running")

    async def step(self, session_id: str, shot: Screenshot, client_result: ClientResult) -> ReplayResponse:
        sess = self.sessions.get(session_id)
        async with sess.lock:
            if sess.state in TERMINAL_STATES:
                return self._response(sess, None)
            if sess.state in (S.WAITING_FOR_CONFIRMATION, S.ASKING_USER):
                return self._response(sess, sess.last_action)  # still waiting: repeat the question
            if sess.state not in (S.EXECUTING_ACTION, S.WAITING_FOR_SCREEN):
                raise ApiError(ErrorCode.SESSION_STATE_INVALID, f"cannot take a screenshot in {sess.state.value}",
                               status=409)
            t0 = time.perf_counter()
            calls0 = self.llm.calls
            before = sess.state
            try:
                action = await self._on_screen(sess, shot, client_result)
            except ApiError as exc:
                if exc.code == ErrorCode.SEGMENTATION_FAILED:
                    # Keep the session alive: the client can resend a screenshot.
                    raise
                sess.transition(S.FAILED, exc.message)
                sess.message = exc.message
                action = None
            sess.llm_calls += self.llm.calls - calls0
            self._log(sess, action, int((time.perf_counter() - t0) * 1000), self.llm.calls - calls0)
            self._report(sess, before)
            return self._response(sess, action)

    async def confirm(self, req: ReplayConfirmRequest) -> ReplayResponse:
        sess = self.sessions.get(req.session_id)
        async with sess.lock:
            pc = sess.confirmation
            if sess.state not in (S.WAITING_FOR_CONFIRMATION, S.ASKING_USER) or pc is None:
                raise ApiError(ErrorCode.CONFIRMATION_STALE, "no confirmation is pending for this session", status=409)
            if req.confirmation_id is not None and req.confirmation_id != pc.id:
                raise ApiError(ErrorCode.CONFIRMATION_STALE, "confirmation_id does not match the pending question",
                               status=409)
            sess.confirmation = None
            if not req.confirmed:
                before = sess.state
                sess.transition(S.STOPPED, f"user declined {pc.kind}")
                sess.message = "Stopped: you declined to continue."
                self._report(sess, before)
                return self._response(sess, self._set_last(sess, Action(action=ActionType.STOP, reason=sess.message)))
            if time.time() - pc.created_at > self.s.confirmation_ttl_seconds:
                sess.transition(S.WAITING_FOR_SCREEN, "confirmation expired")
                sess.message = "Confirmation expired; re-checking the screen."
                return self._response(sess, self._capture(sess, 0))
            if pc.kind == "payment_gate":
                sess.approved_gate = (pc.step_idx, norm(pc.element_text), time.time())
            elif pc.kind in ("flow_ask", "input_fallback"):
                sess.step_idx = pc.step_idx + 1
                if pc.kind == "input_fallback":
                    sess.echo_text = pc.element_text
            sess.pending = None
            sess.retries = sess.scrolls = sess.dismissals = 0
            sess.transition(S.WAITING_FOR_SCREEN, f"user confirmed {pc.kind}")
            sess.message = None
            return self._response(sess, self._capture(sess, 0))

    async def stop(self, session_id: str) -> ReplayResponse:
        sess = self.sessions.get(session_id)
        async with sess.lock:
            if sess.state not in TERMINAL_STATES:
                before = sess.state
                sess.transition(S.STOPPED, "stopped by client")
                sess.message = "Stopped."
                self._report(sess, before)
            return self._response(sess, Action(action=ActionType.STOP, reason=sess.message or "stopped"))

    # ------------------------------------------------------------------------- screen handling

    async def _on_screen(self, sess: ReplaySession, shot: Screenshot, client_result: ClientResult) -> Optional[Action]:
        pending = sess.pending
        if pending and client_result != ClientResult.OK:
            early = self._client_failure(sess, pending, client_result)
            if early is not None or sess.state in TERMINAL_STATES:
                return early

        sess.transition(S.ANALYZING_SCREEN, "screenshot received")
        screen = await self.seg.analyze(shot)

        blocker = detect_blocker(screen, strong_only=True)
        if blocker:
            return self._blocked(sess, blocker, screen)

        if sess.echo_text:
            self._remember_input(sess, sess.echo_text, screen)
            sess.echo_text = ""

        if pending:
            sess.transition(S.VERIFYING, f"checking {pending.kind}")
            ok = self._verify(sess, pending, screen, client_result)
            if ok and pending.kind == "dismiss" and sess.interrupted:
                # The blocker is gone: now check the step it interrupted, on this screen.
                pending, sess.interrupted = sess.interrupted, None
                sess.pending = pending
                ok = self._verify(sess, pending, screen, ClientResult.OK)
            if ok:
                sess.pending = None
                if pending.action.action == ActionType.TYPE:
                    self._remember_input(sess, pending.action.text or "", screen)
                if pending.kind == "step":
                    sess.step_idx = pending.step_idx + 1
                    sess.retries = sess.scrolls = sess.dismissals = 0
                sess.transition(S.NEXT_STEP, "verified")
                return await self._advance(sess, screen, shot)
            return await self._retry(sess, pending, screen, shot, "verification failed")

        sess.transition(S.NEXT_STEP, "fresh screen")
        return await self._advance(sess, screen, shot)

    @staticmethod
    def _remember_input(sess: ReplaySession, typed: str, screen: Screen) -> None:
        """The topmost element whose text is the typed value is the input field echo."""
        t = norm(typed)
        # Input fields sit near the top; a lower match is more likely a result than the echo.
        echoes = [e for e in screen.elements
                  if t and e.bbox[3] <= 0.25 * screen.height and text_score(t, norm(e.text)) >= 0.9]
        if echoes:
            sess.typed_box, sess.typed_text = min(echoes, key=lambda e: (e.bbox[1], e.bbox[0])).bbox, t

    @staticmethod
    def _excluded(sess: ReplaySession, screen: Screen) -> frozenset[int]:
        if not sess.typed_box:
            return frozenset()
        x1, y1, x2, y2 = sess.typed_box
        out = set()
        for e in screen.elements:
            ix = max(0, min(e.bbox[2], x2) - max(e.bbox[0], x1))
            iy = max(0, min(e.bbox[3], y2) - max(e.bbox[1], y1))
            area = max(1, (e.bbox[2] - e.bbox[0]) * (e.bbox[3] - e.bbox[1]))
            if ix * iy / area >= 0.6 and text_score(sess.typed_text, norm(e.text)) >= 0.9:
                out.add(e.index)
        return frozenset(out)

    def _client_failure(self, sess: ReplaySession, pending: Pending, result: ClientResult) -> Optional[Action]:
        if result == ClientResult.INPUT_NOT_SUPPORTED and pending.action.action == ActionType.TYPE:
            sess.transition(S.ANALYZING_SCREEN, "client could not type")
            action = self._ask(sess, "input_fallback", pending.step_idx,
                               f"Text input isn't available on this device. Please type '{pending.action.text}' "
                               "into the field, then confirm.")
            sess.confirmation.element_text = pending.action.text or ""
            return action
        if result == ClientResult.APP_NOT_INSTALLED:
            sess.transition(S.FAILED, "app not installed")
            sess.message = f"{sess.flow.app} is not installed."
            return None
        if result == ClientResult.CAPABILITY_UNAVAILABLE:
            sess.transition(S.FAILED, "android capability unavailable")
            sess.message = ("The Android client could not perform this action (is the accessibility "
                            "service enabled?).")
            return None
        return None  # GESTURE_FAILED: verification will fail and the retry ladder takes over

    def _verify(self, sess: ReplaySession, p: Pending, screen: Screen, result: ClientResult) -> bool:
        if result == ClientResult.GESTURE_FAILED:
            return False
        if p.kind != "step":
            return True
        step = materialize_step(sess.flow.steps[p.step_idx], sess.flow, sess.values)
        if step is None or step.verify.mode == "none":
            return True
        if step.verify.anchors and step.verify.mode in ("anchors", "auto"):
            return all(text_on_screen(a, screen) for a in step.verify.anchors)
        a = step.action
        if a == StepAction.LAUNCH_APP:
            return len(screen.elements) > 0
        if a == StepAction.TYPE:
            return text_on_screen(step.value or "", screen, 0.75) or screen_changed(p.prev_texts, screen, None)
        if a == StepAction.TAP:
            if screen_changed(p.prev_texts, screen, p.element):
                return True
            nxt = self._next_step(sess, p.step_idx + 1)
            if nxt is not None and nxt.action == StepAction.TAP and nxt.target:
                return self.resolver.deterministic(nxt.target, screen, sess.values).found
            return False
        return True  # BACK / SWIPE / WAIT have no reliable visual postcondition

    async def _retry(self, sess: ReplaySession, p: Pending, screen: Screen, shot: Screenshot, why: str) -> Action:
        """Recovery ladder: 1) recapture, 2) re-resolve with text LLM, 3) with vision LLM, then ask."""
        sess.transition(S.RETRYING, why)
        sess.retries += 1
        if sess.retries > self.s.max_retries:
            return self._ask(sess, "retries_exhausted", sess.step_idx, await self._stuck_reason(sess, screen, shot))
        if sess.retries == 1:
            sess.pending = p  # keep verifying the same action after the recapture
            return self._capture(sess, self.s.screen_settle_delay_ms, kind_pending=False,
                                 reason=f"{why}; re-capturing the screen")
        sess.pending = None
        sess.step_idx = p.step_idx if p.kind == "step" else sess.step_idx
        tier = Tier.TEXT_LLM if sess.retries == 2 else Tier.VISION_LLM
        step = self._next_step(sess, sess.step_idx)
        if step is None:
            sess.transition(S.EXECUTING_ACTION, "nothing to retry")
            return self._capture_action(sess, 0)
        if step.action in (StepAction.TAP, StepAction.SWIPE):
            sess.transition(S.RESOLVING_TARGET, f"retry {sess.retries} tier={tier.name}")
            return await self._resolve_tap_or_swipe(sess, step, screen, shot, tier)
        sess.transition(S.EXECUTING_ACTION, f"retry {sess.retries}")
        return self._emit_simple(sess, step, screen)

    # ------------------------------------------------------------------------- stepping

    def _next_step(self, sess: ReplaySession, idx: int) -> Optional[FlowStep]:
        steps = sess.flow.steps
        while idx < len(steps):
            m = materialize_step(steps[idx], sess.flow, sess.values)
            if m is not None:
                return m
            idx += 1
        return None

    async def _advance(self, sess: ReplaySession, screen: Optional[Screen], shot: Optional[Screenshot]) -> Optional[Action]:
        """In NEXT_STEP: skip steps whose optional slots are empty, then emit the current step."""
        steps = sess.flow.steps
        while sess.step_idx < len(steps) and materialize_step(steps[sess.step_idx], sess.flow, sess.values) is None:
            sess.step_idx += 1
        if sess.step_idx >= len(steps):
            sess.transition(S.COMPLETED, "all steps done")
            sess.message = "Flow completed."
            return self._set_last(sess, Action(action=ActionType.STOP, reason=sess.message))
        step = materialize_step(steps[sess.step_idx], sess.flow, sess.values)
        a = step.action
        if a == StepAction.STOP:
            sess.transition(S.COMPLETED, "flow STOP step")
            sess.message = step.reason or "Flow completed."
            return self._set_last(sess, Action(action=ActionType.STOP, reason=sess.message))
        if a == StepAction.ASK_USER:
            return self._ask(sess, "flow_ask", sess.step_idx, step.reason or "Please confirm to continue.")
        if a == StepAction.LAUNCH_APP:
            sess.transition(S.LAUNCHING_APP, step.app or sess.flow.app)
            sess.transition(S.EXECUTING_ACTION, "launch")
            app = step.app or sess.flow.app
            pkg = sess.flow.package if app.casefold() == sess.flow.app.casefold() and sess.flow.package else None
            action = Action(action=ActionType.LAUNCH_APP, app=app, package=pkg or self.s.package_for(app),
                            settle_ms=self.s.launch_settle_delay_ms, reason=f"launch {app}")
            return self._issue(sess, "step", action, screen, None)
        if a in (StepAction.TAP, StepAction.SWIPE):
            if screen is None:
                sess.transition(S.WAITING_FOR_SCREEN, "need a screenshot to resolve the target")
                return self._set_last(sess, Action(action=ActionType.WAIT, duration_ms=0, settle_ms=0,
                                                   reason="capture the screen"))
            sess.transition(S.RESOLVING_TARGET, f"step {sess.step_no}")
            return await self._resolve_tap_or_swipe(sess, step, screen, shot, Tier.DETERMINISTIC)
        sess.transition(S.EXECUTING_ACTION, f"step {sess.step_no}")
        return self._emit_simple(sess, step, screen)

    def _emit_simple(self, sess: ReplaySession, step: FlowStep, screen: Optional[Screen]) -> Action:
        settle = self.s.screen_settle_delay_ms
        if step.action == StepAction.TYPE:
            action = Action(action=ActionType.TYPE, text=step.value, submit=step.submit, settle_ms=settle,
                            reason=step.reason or "type text")
        elif step.action == StepAction.BACK:
            action = Action(action=ActionType.BACK, settle_ms=settle, reason=step.reason or "back")
        else:  # WAIT
            action = Action(action=ActionType.WAIT, duration_ms=step.duration_ms or settle, settle_ms=0,
                            reason=step.reason or "wait")
        return self._issue(sess, "step", action, screen, None)

    async def _resolve_tap_or_swipe(self, sess: ReplaySession, step: FlowStep, screen: Screen,
                                    shot: Optional[Screenshot], min_tier: Tier) -> Action:
        """In RESOLVING_TARGET."""
        if step.action == StepAction.SWIPE:
            gate = gate_reason(step, None, screen)
            if gate and not self._gate_approved(sess, ""):
                return self._gate(sess, gate, "")
            sess.transition(S.EXECUTING_ACTION, "swipe")
            return self._issue(sess, "step", self._swipe(step.direction, screen, step.duration_ms), screen, None)

        target = step.target
        exclude = self._excluded(sess, screen)
        det = self.resolver.deterministic(target, screen, sess.values, exclude)
        if det.found and min_tier == Tier.DETERMINISTIC:
            res = det
        elif det.candidates or sess.retries >= 1 or min_tier > Tier.DETERMINISTIC:
            # Ambiguous, or a retry: escalate. With nothing matching on a first look the screen
            # may still be loading, so recapture first instead of spending LLM calls.
            res = (await self.resolver.resolve(target, screen, sess.values, shot, min_tier=min_tier,
                                               exclude=exclude)).result
        else:
            res = det

        if not res.found:
            blocker = detect_blocker(screen, strong_only=False)
            if blocker:
                return self._blocked(sess, blocker, screen)
            if target.scroll_search and sess.scrolls < self.s.max_scroll_attempts:
                sess.scrolls += 1
                sess.transition(S.EXECUTING_ACTION, f"scrolling to find '{target.description}'")
                return self._issue(sess, "scroll", self._swipe("up", screen, None), screen, None)
            if step.optional and sess.retries >= 1:
                sess.step_idx += 1
                sess.retries = sess.scrolls = sess.dismissals = 0
                sess.transition(S.NEXT_STEP, f"optional step skipped: {res.reason}")
                return await self._advance(sess, screen, shot)
            return await self._not_found(sess, screen, shot, res.reason)

        element = screen.elements[res.index] if res.index is not None else None
        gate = gate_reason(step, element, screen)
        label = element.text if element else (target.text or target.description)
        if gate and not self._gate_approved(sess, label):
            return self._gate(sess, gate, label)
        sess.transition(S.EXECUTING_ACTION, res.reason)
        sess.typed_box, sess.typed_text = None, ""
        action = Action(action=ActionType.TAP, x=res.x, y=res.y, screen_width=screen.width,
                        screen_height=screen.height, confidence=res.confidence, method=res.method,
                        settle_ms=self.s.screen_settle_delay_ms, reason=res.reason)
        return self._issue(sess, "step", action, screen, element)

    async def _not_found(self, sess: ReplaySession, screen: Screen, shot: Optional[Screenshot], why: str) -> Action:
        """RESOLVING_TARGET -> RETRYING: recapture (the next resolution escalates to the LLM tiers)."""
        sess.retries += 1
        sess.transition(S.RETRYING, f"target not found: {why}")
        sess.pending = None
        if sess.retries > self.s.max_retries:
            return self._ask(sess, "retries_exhausted", sess.step_idx, await self._stuck_reason(sess, screen, shot))
        return self._capture(sess, self.s.screen_settle_delay_ms, kind_pending=False,
                             reason=f"target not found ({why}); re-capturing")

    # ------------------------------------------------------------------------- gate / blockers / asking

    def _gate_approved(self, sess: ReplaySession, label: str) -> bool:
        ap = sess.approved_gate
        if not ap:
            return False
        step_idx, approved_label, at = ap
        fresh = time.time() - at <= self.s.confirmation_ttl_seconds
        same = step_idx == sess.step_idx and (not approved_label or text_score(approved_label, norm(label)) >= 0.9)
        if fresh and same:
            sess.approved_gate = None  # one use only
            return True
        return False

    def _gate(self, sess: ReplaySession, why: str, label: str) -> Action:
        sess.transition(S.WAITING_FOR_CONFIRMATION, why)
        reason = ("The flow has reached the final order/payment confirmation "
                  f"({why}). Please confirm before continuing.")
        cid = f"conf_{secrets.token_hex(8)}"
        sess.confirmation = Confirmation(cid, "payment_gate", sess.step_idx, reason, time.time(), label)
        sess.pending = None
        return self._set_last(sess, Action(action=ActionType.ASK_USER, reason=reason, confirmation_id=cid,
                                           kind="payment_gate"))

    def _blocked(self, sess: ReplaySession, b: Blocker, screen: Screen) -> Action:
        sess.transition(S.BLOCKED, b.kind)
        if not b.needs_user and b.dismiss is not None and sess.dismissals < self.s.max_blocker_dismissals:
            sess.dismissals += 1
            x, y = b.dismiss.center
            sess.transition(S.EXECUTING_ACTION, b.reason)
            action = Action(action=ActionType.TAP, x=x, y=y, screen_width=screen.width, screen_height=screen.height,
                            confidence=1.0, method="blocker", settle_ms=self.s.screen_settle_delay_ms, reason=b.reason)
            if sess.pending and sess.pending.kind == "step":
                sess.interrupted = sess.pending
            sess.pending = Pending("dismiss", sess.step_idx, action, b.dismiss, screen_texts(screen))
            return self._set_last(sess, action)
        reason = b.reason if b.needs_user else f"{b.reason} (could not clear it automatically)"
        return self._ask(sess, "blocker", sess.step_idx, reason + " Confirm once it's handled, or decline to stop.")

    def _ask(self, sess: ReplaySession, kind: str, step_idx: int, reason: str) -> Action:
        sess.transition(S.ASKING_USER, kind)
        cid = f"conf_{secrets.token_hex(8)}"
        sess.confirmation = Confirmation(cid, kind, step_idx, reason, time.time())
        sess.pending = None
        sess.message = reason
        return self._set_last(sess, Action(action=ActionType.ASK_USER, reason=reason, confirmation_id=cid, kind=kind))

    async def _stuck_reason(self, sess: ReplaySession, screen: Screen, shot: Optional[Screenshot]) -> str:
        step = self._next_step(sess, sess.step_idx)
        what = step.target.description if step and step.target else f"step {sess.step_no}"
        base = f"Unable to complete step {sess.step_no} ({what}) after {self.s.max_retries} attempts."
        if shot is not None and self.s.vision_llm_enabled:
            jpeg, scale = shot.as_jpeg(80, self.s.vision_image_width)
            out = await self.llm.screen_readiness(jpeg, round(shot.width * scale), round(shot.height * scale))
            if out and not out.ready and out.blocker:
                base += f" The screen seems blocked by: {out.blocker}."
        return base + " Please fix the screen and confirm, or decline to stop."

    # ------------------------------------------------------------------------- helpers

    def _swipe(self, direction: Optional[str], screen: Screen, duration: Optional[int]) -> Action:
        w, h = screen.width, screen.height
        cx, cy = w // 2, h // 2
        coords = {
            "up": (cx, int(h * 0.75), cx, int(h * 0.30)),     # content moves up: see what's below
            "down": (cx, int(h * 0.30), cx, int(h * 0.75)),
            "left": (int(w * 0.80), cy, int(w * 0.20), cy),
            "right": (int(w * 0.20), cy, int(w * 0.80), cy),
        }[direction or "up"]
        x1, y1, x2, y2 = coords
        return Action(action=ActionType.SWIPE, x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration or 400,
                      screen_width=w, screen_height=h, settle_ms=self.s.screen_settle_delay_ms,
                      reason=f"swipe {direction or 'up'}")

    def _issue(self, sess: ReplaySession, kind: str, action: Action, screen: Optional[Screen],
               element: Optional[Element]) -> Action:
        sess.pending = Pending(kind, sess.step_idx, action, element, screen_texts(screen) if screen else [])
        return self._set_last(sess, action)

    def _capture_action(self, sess: ReplaySession, delay: int, reason: str = "capture the screen") -> Action:
        return self._set_last(sess, Action(action=ActionType.WAIT, duration_ms=delay, settle_ms=0, reason=reason))

    def _capture(self, sess: ReplaySession, delay: int, *, kind_pending: bool = False,
                 reason: str = "capture the screen") -> Action:
        """Ask the client for a fresh screenshot after ``delay`` ms (state: EXECUTING_ACTION or
        WAITING_FOR_SCREEN, both of which accept the next /replay/step)."""
        if sess.state == S.RETRYING:
            sess.transition(S.EXECUTING_ACTION, reason)
        return self._capture_action(sess, delay, reason)

    @staticmethod
    def _set_last(sess: ReplaySession, action: Action) -> Action:
        sess.last_action = action
        return action

    def _response(self, sess: ReplaySession, action: Optional[Action], status: Optional[str] = None) -> ReplayResponse:
        st = sess.state
        if status is None:
            status = {S.COMPLETED: "completed", S.FAILED: "failed", S.STOPPED: "stopped",
                      S.WAITING_FOR_CONFIRMATION: "waiting_for_user", S.ASKING_USER: "waiting_for_user"}.get(st, "continue")
        if status == "running" and st in (S.WAITING_FOR_CONFIRMATION, S.ASKING_USER):
            status = "waiting_for_user"
        if status == "running" and st == S.COMPLETED:
            status = "completed"
        if action is None and st == S.FAILED:
            action = Action(action=ActionType.STOP, reason=sess.message or "replay failed")
        return ReplayResponse(session_id=sess.session_id, flow_id=sess.flow.flow_id, step=sess.step_no,
                              total_steps=len(sess.flow.steps), state=st, status=status, action=action,
                              next_action=action, message=sess.message, llm_calls=sess.llm_calls)

    def _log(self, sess: ReplaySession, action: Optional[Action], latency_ms: int, llm_calls: int) -> None:
        a = action.action.value if action else "-"
        target = ""
        step = sess.flow.steps[min(sess.step_idx, len(sess.flow.steps) - 1)]
        if step.target:
            target = step.target.description[:60]  # template text: slot values are not logged
        log.info('replay session=%s flow=%s step=%d state=%s action=%s target="%s" method=%s confidence=%s '
                 "latency_ms=%d llm_calls=%d retries=%d dismissals=%d", sess.session_id, sess.flow.flow_id,
                 sess.step_no, sess.state.value, a, target, getattr(action, "method", None) or "-",
                 getattr(action, "confidence", None), latency_ms, llm_calls, sess.retries, sess.dismissals)
