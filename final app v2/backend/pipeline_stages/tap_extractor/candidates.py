"""Turn tap-log events into tap candidates: where and when to look for the circle."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config import Config
from log_io import Box, TapLog

log = logging.getLogger(__name__)

CLICK = "click"
KEYBOARD = "keyboard"
WINDOW_CHANGE = "window_change"

HIGH, MEDIUM, LOW = "high", "medium", "low"
_CONFIDENCE_RANK = {HIGH: 2, MEDIUM: 1, LOW: 0}

CLICK_TYPES = ("VIEW_CLICKED", "VIEW_LONG_CLICKED")
# Written by the recorder app itself when its Stop button / notification action is tapped.
STOP_TYPE = "APP_STOP_TAPPED"


@dataclass
class Candidate:
    """A place and time where the log says a tap probably happened."""

    log_event_ms: float
    window: tuple[float, float]  # search [start_ms, end_ms]
    box: Box  # search box, video pixels (already padded)
    source: str  # click | keyboard | window_change
    confidence: str  # high | medium | low
    element: dict = field(default_factory=dict)  # text / resourceId / className
    log_types: list[str] = field(default_factory=list)
    # The finger was up by this time (a click fires on finger-up), or None if unknown.
    lift_by_ms: float | None = None


def _pad(box: Box, pad: float, screen: Box) -> Box:
    """Grow ``box`` by ``pad`` on every side, clipped to ``screen``."""
    l, t, r, b = box
    return (max(screen[0], l - pad), max(screen[1], t - pad), min(screen[2], r + pad), min(screen[3], b + pad))


def _overlap(a: Box, b: Box) -> bool:
    """True if the two boxes intersect."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _text(value: object) -> str | None:
    """First non-empty string of a log ``text`` field (string or list), else None."""
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return value or None


def _element(event: dict, node: dict | None) -> dict:
    """Describe the tapped element: text, resourceId, className."""
    node = node or {}
    return {
        "text": _text(node.get("text")) or node.get("contentDescription")
        or event.get("contentDescription") or _text(event.get("text")),
        "resourceId": node.get("resourceId"),
        "className": node.get("className") or event.get("className"),
    }


def click_candidates(tap_log: TapLog, cfg: Config) -> list[Candidate]:
    """One candidate per VIEW_CLICKED / VIEW_LONG_CLICKED (and the app's own Stop tap)."""
    pad = cfg.scaled(cfg.box_padding_px, tap_log.video_width)
    before, after = cfg.click_window_ms
    out: list[Candidate] = []
    for e in tap_log.events:
        etype = e.get("type")
        if etype == STOP_TYPE:
            pass  # our own Stop tap: the only own-app event we keep
        elif etype not in CLICK_TYPES or e.get("packageName") == cfg.own_package:
            continue
        t = float(e["videoTimeMs"])
        ancestor, source = e.get("clickableAncestor"), e.get("source")
        node = ancestor if ancestor and tap_log.box(ancestor.get("bounds")) else source
        box = tap_log.box((node or {}).get("bounds"))
        out.append(Candidate(
            log_event_ms=t,
            window=(t - before, t + after),
            box=_pad(box, pad, tap_log.screen) if box else tap_log.screen,
            source=CLICK,
            confidence=HIGH if box else MEDIUM,
            # Unpadded bounds of the clicked element (video pixels), for grounding.
            element={**_element(e, node), "bounds": [round(v) for v in box] if box else None},
            log_types=[etype],
            # VIEW_LONG_CLICKED fires while the finger is still down.
            lift_by_ms=None if etype == "VIEW_LONG_CLICKED" else t + cfg.click_lift_slack_ms,
        ))
    return out


def _snapshot_before(tap_log: TapLog, t: float) -> dict | None:
    """Latest keyboard snapshot at or before ``t`` if the keyboard was visible then."""
    snap = None
    for s in tap_log.keyboard_snapshots:
        if s.get("videoTimeMs", 0) > t:
            break
        snap = s
    return snap if snap and snap.get("visible") else None


def _key_box(tap_log: TapLog, snap: dict, char: str) -> Box | None:
    """Box of the key labelled ``char`` (case-insensitive); the largest if several match.

    Gboard lists small hint labels (e.g. "1" in the corner of "q") as separate keys,
    so the largest match is the real key.
    """
    boxes = [tap_log.box(k.get("bounds")) for k in snap.get("keys") or []
             if str(k.get("label", "")).lower() == char.lower()]
    boxes = [b for b in boxes if b]
    return max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if boxes else None


def _backspace_box(tap_log: TapLog, snap: dict) -> Box | None:
    """Box of the backspace key, which the snapshot does not list (it has no label).

    On a QWERTY layout it fills the bottom letter row to the right of "m": from m's right
    edge to the keyboard's right edge, at m's height.
    """
    m_box, kb_box = _key_box(tap_log, snap, "m"), tap_log.box(snap.get("bounds"))
    if not m_box or not kb_box or kb_box[2] - m_box[2] < (m_box[2] - m_box[0]) / 2:
        return None
    return (m_box[2], m_box[1], kb_box[2], m_box[3])


def _deleted_count(e: dict) -> int:
    """Characters a TEXT_CHANGED event removed without inserting any (a backspace), else 0."""
    if e.get("addedCount") != 0 or not e.get("removedCount"):
        return 0
    return int(e["removedCount"])


def _field_after(e: dict) -> str | None:
    """Field text after a TEXT_CHANGED event.

    An emptied field reports its hint (e.g. "Search for apps") as its text, so for a pure
    deletion the result is rebuilt from beforeText instead.
    """
    before, i, removed = e.get("beforeText"), e.get("fromIndex"), e.get("removedCount", 0)
    if _deleted_count(e) and before is not None and i is not None:
        return before[:i] + before[i + removed:]
    return _text(e.get("text"))


def _typed_char(e: dict) -> str | None:
    """The one character a TEXT_CHANGED event inserted, or None if it was not a single-key insert."""
    if e.get("addedCount") != 1 or e.get("removedCount", 0) != 0:
        return None
    before, after = e.get("beforeText"), _text(e.get("text"))
    i = e.get("fromIndex")
    if before is None or after is None or i is None or len(after) != len(before) + 1:
        return None
    if not (0 <= i < len(after)) or after[:i] + after[i + 1:] != before:
        return None
    return after[i]


def keyboard_candidates(tap_log: TapLog, cfg: Config) -> list[Candidate]:
    """One candidate per VIEW_TEXT_CHANGED typed while the on-screen keyboard was visible.

    A single inserted character searches that key's box, and a single-character deletion
    searches the backspace key (``className: "backspace"``, ``text: None``); anything else
    (a suggestion or autocomplete inserting several characters, a key not in the snapshot)
    searches the whole keyboard with medium confidence. Every element also records the
    field text before and after the change (``field_before`` / ``field_after``).
    """
    pad = cfg.scaled(cfg.box_padding_px, tap_log.video_width)
    before, after = cfg.keyboard_window_ms
    out: list[Candidate] = []
    for e in tap_log.events:
        if e.get("type") != "VIEW_TEXT_CHANGED" or e.get("packageName") == cfg.own_package:
            continue
        if e.get("isPassword"):
            continue  # no text, so no way to tell which key
        t = float(e["videoTimeMs"])
        snap = _snapshot_before(tap_log, t)
        kb_box = tap_log.box(snap.get("bounds")) if snap else None
        if kb_box is None:
            continue  # keyboard not on screen: text was set by the app, not typed
        char = _typed_char(e)
        key_box = _key_box(tap_log, snap, char) if char else None
        bs_box = _backspace_box(tap_log, snap) if _deleted_count(e) == 1 else None
        if key_box:
            box, conf, text, cls = _pad(key_box, pad, tap_log.screen), HIGH, char, "key"
        elif bs_box:
            box, conf, text, cls = _pad(bs_box, pad, tap_log.screen), HIGH, None, "backspace"
        else:
            # Clip to the screen: the IME window can extend below it.
            box, conf, cls = _pad(kb_box, 0, tap_log.screen), MEDIUM, "keyboard"
            text = _text(e.get("text"))
        out.append(Candidate(
            log_event_ms=t,
            window=(t - before, t + after),
            box=box,
            source=KEYBOARD,
            confidence=conf,
            element={"text": text, "resourceId": None, "className": cls,
                     "field_before": e.get("beforeText"), "field_after": _field_after(e)},
            log_types=["VIEW_TEXT_CHANGED"],
            lift_by_ms=t + cfg.keyboard_lift_slack_ms,
        ))
    return out


def window_change_candidates(tap_log: TapLog, cfg: Config, taken: list[Candidate]) -> list[Candidate]:
    """Whole-screen, low-confidence candidates for new screens with no click/key just before.

    A "new screen" is a WINDOW_STATE_CHANGED with contentChangeTypes 0 whose
    (package, class) differs from the previous one. Keyboard windows are skipped.
    """
    before, after = cfg.window_change_window_ms
    taken_times = [c.log_event_ms for c in taken]
    out: list[Candidate] = []
    last_screen: tuple[str, str] | None = None
    for e in tap_log.events:
        if e.get("type") != "WINDOW_STATE_CHANGED" or e.get("contentChangeTypes", 0) != 0:
            continue
        pkg, cls = e.get("packageName") or "", e.get("className") or ""
        if pkg == cfg.own_package or "inputmethod" in pkg:
            continue
        screen = (pkg, cls)
        is_new = screen != last_screen
        last_screen = screen
        if not is_new:
            continue
        t = float(e["videoTimeMs"])
        if any(t - cfg.window_change_click_gap_ms <= tc <= t for tc in taken_times):
            continue  # already explained by a click or key press
        out.append(Candidate(
            log_event_ms=t,
            window=(t - before, t + after),
            box=tap_log.screen,
            source=WINDOW_CHANGE,
            confidence=LOW,
            element={"text": _text(e.get("text")), "resourceId": None, "className": cls},
            log_types=["WINDOW_STATE_CHANGED"],
        ))
    return out


def merge_candidates(cands: list[Candidate], cfg: Config) -> list[Candidate]:
    """Merge candidates less than ``merge_ms`` apart whose search boxes overlap.

    The higher-confidence candidate keeps its box, source and element; windows are joined.
    """
    out: list[Candidate] = []
    for c in sorted(cands, key=lambda c: c.log_event_ms):
        prev = out[-1] if out else None
        if prev and c.log_event_ms - prev.log_event_ms < cfg.merge_ms and _overlap(prev.box, c.box):
            keep, other = (c, prev) if _CONFIDENCE_RANK[c.confidence] > _CONFIDENCE_RANK[prev.confidence] else (prev, c)
            keep.window = (min(prev.window[0], c.window[0]), max(prev.window[1], c.window[1]))
            keep.log_types = prev.log_types + c.log_types
            lifts = [x.lift_by_ms for x in (prev, c) if x.lift_by_ms is not None]
            keep.lift_by_ms = min(lifts) if lifts else None
            out[-1] = keep
            log.debug("Merged %s candidate at %.0f ms into %s at %.0f ms",
                      other.source, other.log_event_ms, keep.source, keep.log_event_ms)
        else:
            out.append(c)
    return out


def build_candidates(tap_log: TapLog, cfg: Config) -> list[Candidate]:
    """All tap candidates from ``tap_log``, merged and in time order."""
    direct = click_candidates(tap_log, cfg) + keyboard_candidates(tap_log, cfg)
    windows = window_change_candidates(tap_log, cfg, direct)
    merged = merge_candidates(direct + windows, cfg)
    log.info("Log candidates: %d click, %d keyboard, %d window-change -> %d after merging",
             sum(c.source == CLICK for c in direct), sum(c.source == KEYBOARD for c in direct),
             len(windows), len(merged))
    return merged
