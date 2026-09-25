"""Ground each tap step of flow.json to a UI element found by the ui-segmentation pipeline.

For every ``tap`` step, the step's before_frame is segmented (segment_ui.py -> ocr_text.py ->
combine_results.py, run as subprocesses; results are cached in ``segment_out/`` next to the
frame) and the tap point is matched against the detected element boxes. The chosen element is
attached as ``grounded_element``; the step's original ``element`` / ``element_trustworthy``
are left untouched so downstream code can compare both sources. ``type_text`` steps pass
through unchanged. Pure geometry: no LLM, no network.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEGMENT_OUT = "segment_out"
CONTAINED = "contained"
FALLBACK_NEAREST = "fallback_nearest"
REQUIRED_TAP_KEYS = ("step_index", "tap_x", "tap_y", "before_frame")


class GroundingError(Exception):
    """Raised for missing/malformed inputs or a failed segmentation script."""


@dataclass
class ReviewFlag:
    """A step where the log element's text and the grounded element's text disagree."""

    step_index: int
    element_text: str
    grounded_text: str


@dataclass
class GroundResult:
    """Grounded steps plus counts for the summary."""

    steps: list[dict[str, Any]]
    contained: int = 0
    fallback_nearest: int = 0
    skipped: int = 0
    segmented_frames: int = 0
    reused_frames: int = 0
    review_flags: list[ReviewFlag] = field(default_factory=list)


# --------------------------------------------------------------------------- loading

def load_flow(path: Path) -> list[dict[str, Any]]:
    """Load flow.json and validate that tap steps carry what grounding needs."""
    if not path.is_file():
        raise GroundingError(f"flow.json not found: {path}")
    try:
        steps = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise GroundingError(f"cannot read flow.json {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GroundingError(f"flow.json {path} is not valid JSON: {exc}") from exc
    if not isinstance(steps, list):
        raise GroundingError(f"flow.json {path} must contain a JSON list, got {type(steps).__name__}")
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "type" not in step:
            raise GroundingError(f"{path}: entry {i} is not a step object with a 'type'")
        if step["type"] == "tap":
            missing = [k for k in REQUIRED_TAP_KEYS if k not in step]
            if missing:
                raise GroundingError(f"{path}: tap step {i} is missing {missing}")
    return steps


def load_combined(path: Path) -> list[dict[str, Any]]:
    """Load a ``*_combined.json`` and return its elements with normalized fields.

    Accepts either a bare element list or combine_results.py's ``{"elements": [...]}``
    object, and either ``parent_id`` or ``parent`` for the parent link. Each returned element
    has ``id``, ``type``, ``text`` (None when empty), ``bbox``, ``center`` and ``parent_id``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GroundingError(f"cannot read segmentation output {path}: {exc}") from exc
    raw = data.get("elements") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise GroundingError(f"{path}: expected a list of elements (or an object with 'elements')")

    elements = []
    for i, e in enumerate(raw):
        try:
            x1, y1, x2, y2 = (float(v) for v in e["bbox"])
            center = e.get("center") or [(x1 + x2) / 2, (y1 + y2) / 2]
            elements.append({
                "id": e["id"],
                "type": e.get("type"),
                "text": e.get("text") or None,
                "bbox": list(e["bbox"]),
                "center": list(center),
                "parent_id": e.get("parent_id", e.get("parent")),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise GroundingError(f"{path}: element {i} is malformed ({exc!r})") from exc
    return elements


# --------------------------------------------------------------------------- segmentation

def combined_path_for(frame: Path) -> Path:
    """Where combine_results.py writes the result for ``frame``."""
    return frame.parent / SEGMENT_OUT / f"{frame.stem}_combined.json"


def run_segmentation(frame: Path, scripts_dir: Path, python: str) -> Path:
    """Run segment_ui.py, ocr_text.py and combine_results.py on ``frame``; return the combined path.

    Output goes to ``segment_out/`` next to the frame. Each script's exit code is checked;
    on failure its stderr is printed and a GroundingError names the script.
    """
    out_dir = frame.parent / SEGMENT_OUT
    image = str(frame)
    commands = [
        ("segment_ui.py", [image, "--crops", "-o", str(out_dir)]),
        ("ocr_text.py", [image, "-o", str(out_dir)]),
        ("combine_results.py", [image, "-d", str(out_dir)]),
    ]
    # The scripts print element text (e.g. "₹"); force UTF-8 so a Windows console codepage
    # can't crash them.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for script, args in commands:
        script_path = scripts_dir / script
        if not script_path.is_file():
            raise GroundingError(f"segmentation script not found: {script_path}")
        proc = subprocess.run([python, str(script_path), *args], cwd=scripts_dir, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"--- {script} stderr ---\n{proc.stderr.rstrip()}\n---", file=sys.stderr)
            raise GroundingError(f"{script} failed (exit {proc.returncode}) on {frame}")

    combined = combined_path_for(frame)
    if not combined.is_file():
        raise GroundingError(f"combine_results.py succeeded but {combined} was not written")
    return combined


# --------------------------------------------------------------------------- geometry

def _contains(bbox: list[float], x: float, y: float) -> bool:
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def _area(bbox: list[float]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _ancestors(el: dict[str, Any], by_id: dict[Any, dict[str, Any]]) -> set[Any]:
    """ids of all ancestors of ``el`` (cycle-safe)."""
    seen: set[Any] = set()
    pid = el["parent_id"]
    while pid is not None and pid not in seen and pid in by_id:
        seen.add(pid)
        pid = by_id[pid]["parent_id"]
    return seen


def pick_element(elements: list[dict[str, Any]], x: float, y: float) -> tuple[dict[str, Any], str] | None:
    """Choose the element for a tap at (x, y); returns (element, confidence) or None if empty.

    Among elements whose bbox contains the point, any that is an ancestor of another match is
    dropped (children are more specific, regardless of area); the smallest-area survivor wins.
    With no containing element, the element whose center is nearest the point is used.
    """
    if not elements:
        return None
    matches = [e for e in elements if _contains(e["bbox"], x, y)]
    if matches:
        by_id = {e["id"]: e for e in elements}
        ancestor_ids: set[Any] = set()
        for m in matches:
            ancestor_ids |= _ancestors(m, by_id)
        leaves = [m for m in matches if m["id"] not in ancestor_ids] or matches
        return min(leaves, key=lambda e: (_area(e["bbox"]), str(e["id"]))), CONTAINED
    nearest = min(elements, key=lambda e: math.dist(e["center"], (x, y)))
    return nearest, FALLBACK_NEAREST


def texts_disagree(a: str | None, b: str | None) -> bool:
    """True when both texts are non-empty and neither contains the other (case-insensitive)."""
    if not a or not b:
        return False
    a, b = a.strip().casefold(), b.strip().casefold()
    return bool(a and b) and a not in b and b not in a


# --------------------------------------------------------------------------- driver

def ground_flow(flow_path: Path, out_path: Path, scripts_dir: Path, *, python: str | None = None,
                force_rerun: bool = False, only_untrustworthy: bool = False) -> GroundResult:
    """Ground every tap step of ``flow_path`` and write the result to ``out_path``.

    ``before_frame`` paths are resolved relative to flow.json's folder. ``python`` is the
    interpreter used for the segmentation scripts (default: the current one).
    """
    if not scripts_dir.is_dir():
        raise GroundingError(f"segmentation dir not found: {scripts_dir}")
    scripts_dir = scripts_dir.resolve()  # scripts run with cwd=scripts_dir
    python = python or sys.executable
    steps = load_flow(flow_path)
    base = flow_path.resolve().parent

    # Fail on missing frames before spending minutes on segmentation.
    todo = [s for s in steps if s["type"] == "tap"
            and not (only_untrustworthy and s.get("element_trustworthy") is not False)]
    frames: dict[int, Path] = {}
    for s in todo:
        frame = Path(s["before_frame"])
        frame = (frame if frame.is_absolute() else base / frame).resolve()
        if not frame.is_file():
            raise GroundingError(f"step {s['step_index']}: before_frame not found: {frame}")
        frames[id(s)] = frame

    result = GroundResult(steps=steps)
    result.skipped = sum(1 for s in steps if s["type"] == "tap") - len(todo)
    cache: dict[Path, list[dict[str, Any]]] = {}
    for s in todo:
        frame = frames[id(s)]
        if frame not in cache:
            combined = combined_path_for(frame)
            if force_rerun or not combined.is_file():
                print(f"  segmenting step {s['step_index']}: {frame.name} ...", file=sys.stderr, flush=True)
                combined = run_segmentation(frame, scripts_dir, python)
                result.segmented_frames += 1
            else:
                result.reused_frames += 1
            cache[frame] = load_combined(combined)

        picked = pick_element(cache[frame], float(s["tap_x"]), float(s["tap_y"]))
        if picked is None:
            raise GroundingError(f"step {s['step_index']}: segmentation found no elements in {frame}")
        el, confidence = picked
        s["grounded_element"] = {
            "id": el["id"], "type": el["type"], "text": el["text"],
            "bbox": el["bbox"], "center": el["center"], "grounded_confidence": confidence,
        }
        if confidence == CONTAINED:
            result.contained += 1
        else:
            result.fallback_nearest += 1

        log_text = (s.get("element") or {}).get("text")
        if texts_disagree(log_text, el["text"]):
            result.review_flags.append(ReviewFlag(s["step_index"], log_text, el["text"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(steps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
