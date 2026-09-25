"""Turn extracted taps (taps.json) into an ordered list of semantic steps (flow.json).

Pure data transformation for the flow abstractor: consecutive keystrokes collapse into one
``type_text`` step (with keystrokes the video missed spliced back in from events_report.json),
every other tap passes through as a ``tap`` step. The before/touch/after frames each step
refers to are copied into a frames folder next to flow.json so the flow is self-contained.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEYBOARD = "keyboard"
WINDOW_CHANGE = "window_change"
TRUSTWORTHY_SOURCES = frozenset({"click", KEYBOARD})
# events_report.json marks candidates the video search missed with this label / status.
NOT_FOUND_LABEL = "NOT_FOUND"
NOT_FOUND_STATUS = "not_found_in_video"

REQUIRED_TAP_KEYS = ("id", "tap_x", "tap_y", "touch_ms", "before", "touch", "after", "source")
FRAME_KINDS = ("before", "touch", "after")


class FlowBuildError(Exception):
    """Raised when an input file is missing or malformed, or a referenced frame is missing."""


@dataclass
class FlowResult:
    """Built steps plus where they were written."""

    steps: list[dict[str, Any]]
    out_path: Path | None = None
    frames_dir: Path | None = None
    copied_frames: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        """Counts for the human-readable summary."""
        taps = [s for s in self.steps if s["type"] == "tap"]
        return {
            "total_steps": len(self.steps),
            "type_text": sum(1 for s in self.steps if s["type"] == "type_text"),
            "tap": len(taps),
            "needs_visual_grounding": sum(1 for s in taps if not s["element_trustworthy"]),
            "reconstructed_chars": sum(
                sum(1 for c in s["characters"] if c["reconstructed_from_log"])
                for s in self.steps if s["type"] == "type_text"),
        }


# --------------------------------------------------------------------------- loading

def _read_json_list(path: Path, what: str) -> list[Any]:
    """Read ``path`` and return its top-level JSON list, or raise FlowBuildError."""
    if not path.is_file():
        raise FlowBuildError(f"{what} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise FlowBuildError(f"cannot read {what} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FlowBuildError(f"{what} {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise FlowBuildError(f"{what} {path} must contain a JSON list, got {type(data).__name__}")
    return data


def load_taps(path: Path) -> list[dict[str, Any]]:
    """Load taps.json, validate each entry and return them sorted by ``id``."""
    taps = _read_json_list(path, "taps.json")
    for i, tap in enumerate(taps):
        if not isinstance(tap, dict):
            raise FlowBuildError(f"{path}: entry {i} is not an object")
        missing = [k for k in REQUIRED_TAP_KEYS if k not in tap]
        if missing:
            hint = " (was it extracted with --log? 'source' only exists in log-guided output)" \
                if missing == ["source"] else ""
            raise FlowBuildError(f"{path}: tap entry {i} (id={tap.get('id')}) is missing {missing}{hint}")
        if "lift_ms" not in tap:
            if "duration_ms" not in tap:
                raise FlowBuildError(f"{path}: tap id={tap['id']} has neither lift_ms nor duration_ms")
            tap["lift_ms"] = round(float(tap["touch_ms"]) + float(tap["duration_ms"]), 1)
        if tap.get("element") is not None and not isinstance(tap["element"], dict):
            raise FlowBuildError(f"{path}: tap id={tap['id']} has a non-object 'element'")
    ids = [t["id"] for t in taps]
    if len(ids) != len(set(ids)):
        raise FlowBuildError(f"{path}: duplicate tap ids")
    return sorted(taps, key=lambda t: t["id"])


def _is_not_found(event: dict[str, Any]) -> bool:
    return event.get("label") == NOT_FOUND_LABEL or event.get("status") == NOT_FOUND_STATUS


def load_missed_keystrokes(path: Path) -> list[dict[str, Any]]:
    """Load events_report.json and return its NOT_FOUND keyboard events, sorted by log time.

    Only events with a numeric ``log_event_ms`` and a text element are usable for splicing.
    """
    events = _read_json_list(path, "events_report.json")
    missed = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise FlowBuildError(f"{path}: entry {i} is not an object")
        if ev.get("source") != KEYBOARD or not _is_not_found(ev):
            continue
        t = ev.get("log_event_ms")
        text = (ev.get("element") or {}).get("text")
        if isinstance(t, (int, float)) and isinstance(text, str):
            missed.append(ev)
    return sorted(missed, key=lambda e: e["log_event_ms"])


# --------------------------------------------------------------------------- building

def _frame(tap: dict[str, Any], kind: str, taps_dir: Path) -> Path:
    """Absolute path of one of a tap's frames (taps.json paths are relative to its folder)."""
    p = Path(tap[kind])
    return p if p.is_absolute() else taps_dir / p


def _type_text_step(run: list[dict[str, Any]], missed: list[dict[str, Any]],
                    taps_dir: Path) -> dict[str, Any]:
    """Collapse a run of consecutive keyboard taps into one ``type_text`` step."""
    first, last = run[0], run[-1]
    start_ms, end_ms = float(first["touch_ms"]), float(last["lift_ms"])

    chars: list[dict[str, Any]] = [{
        "char": (tap.get("element") or {}).get("text") or "",
        "source_tap_id": tap["id"],
        "log_event_ms": tap.get("log_event_ms"),
        "reconstructed_from_log": False,
    } for tap in run]
    for ev in missed:
        if start_ms <= ev["log_event_ms"] <= end_ms:
            chars.append({
                "char": ev["element"]["text"],
                "source_tap_id": None,
                "log_event_ms": ev["log_event_ms"],
                "reconstructed_from_log": True,
            })
    # Order by log time; a tap without log_event_ms falls back to its touch time. Stable sort
    # keeps id order on ties.
    touch_by_id = {t["id"]: float(t["touch_ms"]) for t in run}
    chars.sort(key=lambda c: c["log_event_ms"] if c["log_event_ms"] is not None
               else touch_by_id[c["source_tap_id"]])

    return {
        "type": "type_text",
        "text": "".join(c["char"] for c in chars),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "before_frame": _frame(first, "before", taps_dir),
        "after_frame": _frame(last, "after", taps_dir),
        "source_tap_ids": [t["id"] for t in run],
        "characters": chars,
        "reconstructed_from_log": any(c["reconstructed_from_log"] for c in chars),
    }


def _tap_step(tap: dict[str, Any], taps_dir: Path) -> dict[str, Any]:
    """Pass a single tap through as a ``tap`` step."""
    return {
        "type": "tap",
        "tap_x": tap["tap_x"],
        "tap_y": tap["tap_y"],
        "touch_ms": float(tap["touch_ms"]),
        "lift_ms": float(tap["lift_ms"]),
        "before_frame": _frame(tap, "before", taps_dir),
        "touch_frame": _frame(tap, "touch", taps_dir),
        "after_frame": _frame(tap, "after", taps_dir),
        "source_tap_id": tap["id"],
        "source": tap["source"],
        "element": tap.get("element"),
        # A window_change tap's element is the destination screen, not the tapped control.
        "element_trustworthy": tap["source"] in TRUSTWORTHY_SOURCES,
    }


def build_steps(taps: list[dict[str, Any]], missed_keystrokes: list[dict[str, Any]],
                taps_dir: Path) -> list[dict[str, Any]]:
    """Collapse keystroke runs, pass other taps through, order by time and number the steps.

    ``taps`` must be sorted by id. Frame fields hold absolute ``Path`` objects; see
    :func:`finalize_frames` for turning them into portable paths.
    """
    steps: list[dict[str, Any]] = []
    i = 0
    while i < len(taps):
        j = i
        while j < len(taps) and taps[j]["source"] == KEYBOARD:
            j += 1
        if j - i >= 2:
            steps.append(_type_text_step(taps[i:j], missed_keystrokes, taps_dir))
            i = j
        else:
            steps.append(_tap_step(taps[i], taps_dir))
            i += 1

    steps.sort(key=lambda s: s["start_ms"] if s["type"] == "type_text" else s["touch_ms"])
    return [{"step_index": n, **s} for n, s in enumerate(steps)]


# --------------------------------------------------------------------------- frames / output

def _frame_keys(step: dict[str, Any]) -> list[str]:
    return [f"{k}_frame" for k in FRAME_KINDS if f"{k}_frame" in step]


def finalize_frames(steps: list[dict[str, Any]], out_path: Path,
                    frames_dir: Path | None) -> int:
    """Rewrite frame paths relative to flow.json, copying them into ``frames_dir`` first.

    With ``frames_dir`` each frame becomes ``step_NNN_<kind>.png`` there (stale ``step_*.png``
    files from an earlier run are removed). With ``None`` the original frames are referenced
    in place. Returns the number of frames copied.
    """
    missing = [str(s[k]) for s in steps for k in _frame_keys(s) if not Path(s[k]).is_file()]
    if missing:
        raise FlowBuildError(f"{len(missing)} referenced frame(s) not found, e.g. {missing[0]}")

    base = out_path.resolve().parent
    copied = 0
    if frames_dir is not None:
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old in frames_dir.glob("step_*.png"):
            old.unlink()
    for step in steps:
        for key in _frame_keys(step):
            src = Path(step[key])
            if frames_dir is not None:
                dst = frames_dir / f"step_{step['step_index']:03d}_{key.removesuffix('_frame')}{src.suffix}"
                shutil.copy2(src, dst)
                copied += 1
                src = dst
            src = src.resolve()
            try:
                step[key] = src.relative_to(base).as_posix()
            except ValueError:
                step[key] = src.as_posix()
    return copied


def build_flow(taps_path: Path, events_report_path: Path, out_path: Path,
               frames_dir: Path | None = None, copy_frames: bool = True) -> FlowResult:
    """Load both inputs, build the steps, store their frames and write flow.json.

    ``frames_dir`` defaults to ``<out stem>_frames`` next to ``out_path`` (``flow_frames/``
    for ``flow.json``). With ``copy_frames=False`` flow.json points at the original frames.
    """
    taps = load_taps(taps_path)
    missed = load_missed_keystrokes(events_report_path)
    steps = build_steps(taps, missed, taps_path.resolve().parent)

    result = FlowResult(steps=steps, out_path=out_path)
    for s in steps:
        if s["type"] == "tap" and s["source"] == KEYBOARD:
            result.warnings.append(
                f"step {s['step_index']}: lone keystroke (tap {s['source_tap_id']}) kept as a tap step")

    if copy_frames:
        result.frames_dir = frames_dir or out_path.parent / f"{out_path.stem}_frames"
    result.copied_frames = finalize_frames(steps, out_path, result.frames_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(steps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
