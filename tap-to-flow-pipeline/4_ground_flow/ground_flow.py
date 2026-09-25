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
LOG_BOUNDS = "log_bounds"
BATCH_SCRIPT = "segment_batch.py"
# A logged click's bounds match a segmented element when their IoU is at least this.
LOG_BOUNDS_MIN_IOU = 0.5
# The vision pick is "too deep" when its area is below this fraction of the logged bounds.
LOG_BOUNDS_MIN_AREA_RATIO = 0.25
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
    log_bounds: int = 0
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


def run_segmentation_batch(frames: list[Path], scripts_dir: Path, python: str) -> None:
    """Segment several frames in one process (models loaded once) with segment_batch.py.

    Frames are grouped by folder so each group writes to its own ``segment_out/``. Progress
    lines are echoed to stderr; on failure the tail of the output is printed and a
    GroundingError is raised.
    """
    script = scripts_dir / BATCH_SCRIPT
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    groups: dict[Path, list[Path]] = {}
    for f in frames:
        groups.setdefault(f.parent / SEGMENT_OUT, []).append(f)
    for out_dir, group in groups.items():
        cmd = [python, str(script), *map(str, group), "--crops", "-o", str(out_dir)]
        proc = subprocess.Popen(cmd, cwd=scripts_dir, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        tail: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-40:]
            if line.startswith("[") or line.startswith("models loaded"):
                print(f"  {line}", file=sys.stderr, flush=True)
        if proc.wait() != 0:
            print(f"--- {BATCH_SCRIPT} output (last lines) ---\n" + "\n".join(tail) + "\n---", file=sys.stderr)
            raise GroundingError(f"{BATCH_SCRIPT} failed (exit {proc.returncode}) on {len(group)} frame(s) in {out_dir}")
        missing = [f for f in group if not combined_path_for(f).is_file()]
        if missing:
            raise GroundingError(f"{BATCH_SCRIPT} succeeded but wrote no combined result for {missing[0]}")


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


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def _inside_frac(inner: list[float], outer: list[float]) -> float:
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = _area(inner)
    return ix * iy / a if a > 0 else 0.0


def _log_bounds_element(elements: list[dict[str, Any]], bounds: list[float]) -> dict[str, Any]:
    """A pseudo-element for the logged click bounds, carrying the text segmented inside them."""
    inside = sorted((e for e in elements if e["text"] and _inside_frac(e["bbox"], bounds) >= 0.9),
                    key=lambda e: (e["bbox"][1], e["bbox"][0]))
    x1, y1, x2, y2 = bounds
    return {"id": None, "type": "log_bounds", "text": " ".join(e["text"] for e in inside) or None,
            "bbox": [round(v) for v in bounds], "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)]}


def pick_element(elements: list[dict[str, Any]], x: float, y: float,
                 log_bounds: list[float] | None = None) -> tuple[dict[str, Any], str] | None:
    """Choose the element for a tap at (x, y); returns (element, confidence) or None if empty.

    Among elements whose bbox contains the point, any that is an ancestor of another match is
    dropped (children are more specific, regardless of area); the smallest-area survivor wins.
    With no containing element, the element whose center is nearest the point is used.

    ``log_bounds`` (the clicked element's bounds from the tap log, when known) refines this:
    a containing element whose IoU with them is at least LOG_BOUNDS_MIN_IOU wins outright;
    and if the vision pick is far smaller than them (or only a nearest-center fallback), the
    detector missed the real target, so a ``log_bounds`` pseudo-element is returned instead.
    """
    if not elements:
        return None
    matches = [e for e in elements if _contains(e["bbox"], x, y)]
    if log_bounds is not None and matches:
        best = max(matches, key=lambda e: _iou(e["bbox"], log_bounds))
        if _iou(best["bbox"], log_bounds) >= LOG_BOUNDS_MIN_IOU:
            return best, CONTAINED
    picked = _pick_geometric(elements, matches, x, y)
    if log_bounds is not None and _contains(log_bounds, x, y) and (
            picked[1] == FALLBACK_NEAREST
            or _area(picked[0]["bbox"]) < LOG_BOUNDS_MIN_AREA_RATIO * _area(log_bounds)):
        return _log_bounds_element(elements, log_bounds), LOG_BOUNDS
    return picked


def _pick_geometric(elements: list[dict[str, Any]], matches: list[dict[str, Any]],
                    x: float, y: float) -> tuple[dict[str, Any], str]:
    """Deepest-then-smallest containing element, else the nearest center."""
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
    unique = list(dict.fromkeys(frames.values()))
    pending = [f for f in unique if force_rerun or not combined_path_for(f).is_file()]
    result.segmented_frames, result.reused_frames = len(pending), len(unique) - len(pending)
    if pending:
        print(f"  segmenting {len(pending)} frame(s) ...", file=sys.stderr, flush=True)
        if (scripts_dir / BATCH_SCRIPT).is_file():
            run_segmentation_batch(pending, scripts_dir, python)
        else:  # older segmentation folder: three scripts per frame
            for f in pending:
                run_segmentation(f, scripts_dir, python)
    cache = {f: load_combined(combined_path_for(f)) for f in unique}

    for s in todo:
        frame = frames[id(s)]
        bounds = (s.get("element") or {}).get("bounds")
        bounds = [float(v) for v in bounds] if isinstance(bounds, list) and len(bounds) == 4 else None
        x, y = float(s["tap_x"]), float(s["tap_y"])
        picked = pick_element(cache[frame], x, y, bounds)
        if picked is None:
            raise GroundingError(f"step {s['step_index']}: segmentation found no elements in {frame}")
        el, confidence = picked
        s["grounded_element"] = {
            "id": el["id"], "type": el["type"], "text": el["text"],
            "bbox": el["bbox"], "center": el["center"], "grounded_confidence": confidence,
        }
        if confidence == LOG_BOUNDS:
            matches = [e for e in cache[frame] if _contains(e["bbox"], x, y)]
            inner, _ = _pick_geometric(cache[frame], matches, x, y)
            s["grounded_element"]["inner_element"] = {k: inner[k] for k in ("id", "type", "text", "bbox", "center")}
            result.log_bounds += 1
        elif confidence == CONTAINED:
            result.contained += 1
        else:
            result.fallback_nearest += 1

        log_text = (s.get("element") or {}).get("text")
        if texts_disagree(log_text, el["text"]):
            result.review_flags.append(ReviewFlag(s["step_index"], log_text, el["text"]))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(steps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result
