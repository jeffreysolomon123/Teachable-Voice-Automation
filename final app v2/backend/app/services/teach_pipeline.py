"""TEACH pipeline: screen recording + tap log -> grounded_flow.json.

  stage 1  tap_extractor   (unchanged copy of tap-to-flow-pipeline)  -> taps.json, taps/*.png
  stage 2  flow_builder    (unchanged copy)                          -> flow.json, flow_frames/*.png
  stage 3  OpenRouter LLM segmentation, all before/after frames in parallel
                                                                     -> flow_frames/segment_out/*_combined.json, *_ocr.json
  stage 4  ground_flow     (unchanged copy)                          -> grounded_flow.json

Stage 4 reads stage 3's files from its segment_out/ cache, so it never runs its own segmentation.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..config import Settings
from .segmentation.base import SegmentationError
from .segmentation.openrouter_vision import segment_bytes

log = logging.getLogger("app.teach.pipeline")

BACKEND_DIR = Path(__file__).resolve().parents[2]
STAGES_DIR = BACKEND_DIR / "pipeline_stages"
SEGMENT_OUT = "segment_out"
# The copied stages use flat imports ("from config import Config"), as in tap-to-flow-pipeline.
for _d in ("tap_extractor", "flow_builder", "ground_flow"):
    if str(STAGES_DIR / _d) not in sys.path:
        sys.path.insert(0, str(STAGES_DIR / _d))

from config import Config as StageConfig  # noqa: E402  (stage 1)
from extractor import extract_taps_with_log  # noqa: E402
from flow_builder import build_flow  # noqa: E402  (stage 2)
from ground_flow import ground_flow  # noqa: E402  (stage 4)


class PipelineError(Exception):
    """A stage failed; the message names the stage."""


def _annotate(img: np.ndarray, elements: list[dict[str, Any]], out_path: Path) -> None:
    img = img.copy()
    scale = max(1, img.shape[1] // 700)
    for e in elements:
        color = (0, 0, 255) if e["interactive"] else ((0, 150, 0) if e["type"] == "text" else (200, 100, 0))
        x1, y1, x2, y2 = e["bbox"]
        cv2.rectangle(img, (x1, y1), (x2, y2), color, scale)
    for e in elements:
        x1, y1 = e["bbox"][:2]
        label = str(e["id"])
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, scale)
        cv2.rectangle(img, (x1, y1), (x1 + tw + 4, y1 + th + 6), (0, 0, 0), -1)
        cv2.putText(img, label, (x1 + 2, y1 + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), scale)
    _, buf = cv2.imencode(".png", img)
    buf.tofile(str(out_path))


def segment_frame(frame: Path, s: Settings) -> dict[str, Any]:
    """Stage 3 for one frame: writes segment_out/<stem>_combined.json and _ocr.json next to it."""
    img = cv2.imdecode(np.fromfile(str(frame), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SegmentationError(f"cannot decode {frame.name}")
    h, w = img.shape[:2]
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    elements, info = segment_bytes(s.openrouter_api_key, s.openrouter_seg_model, s.openrouter_seg_retries,
                                   jpeg.tobytes(), w, h)
    out = frame.parent / SEGMENT_OUT
    out.mkdir(parents=True, exist_ok=True)
    head = {"image": str(frame), "width": w, "height": h}
    (out / f"{frame.stem}_combined.json").write_text(
        json.dumps({**head, **info, "elements": elements}, indent=2, ensure_ascii=False), encoding="utf-8")
    # Text lines for before_text / after_text: leaf elements only (containers repeat their children).
    texts = [{"text": e["text"], "bbox": e["bbox"], "confidence": 1.0}
             for e in elements if e["text"] and not e["children"]]
    (out / f"{frame.stem}_ocr.json").write_text(
        json.dumps({**head, "texts": texts}, indent=2, ensure_ascii=False), encoding="utf-8")
    _annotate(img, elements, out / f"{frame.stem}_combined_annotated.png")
    return {"frame": frame.name, "elements": len(elements), **info}


def segment_frames(frames: list[Path], s: Settings) -> tuple[list[dict], list[str]]:
    """Stage 3 for many frames in parallel; failed frames get one more sequential pass."""
    stats, failed = [], []

    def one(f: Path):
        try:
            return segment_frame(f, s), None
        except Exception as exc:  # noqa: BLE001 - report per frame, keep the others
            return None, f"{f.name}: {exc}"

    workers = max(1, min(s.teach_seg_concurrency, len(frames) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f, (stat, err) in zip(frames, pool.map(one, frames)):
            (failed.append(f) if err else stats.append(stat))
    errors = []
    for f in failed:  # upstream cut-offs come in bursts under parallel load
        stat, err = one(f)
        (errors.append(err) if err else stats.append(stat))
    return stats, errors


def frames_to_segment(flow_path: Path) -> list[Path]:
    base = flow_path.parent
    frames: list[Path] = []
    for st in json.loads(flow_path.read_text(encoding="utf-8")):
        if st.get("type") == "tap":
            for key in ("before_frame", "after_frame"):
                if st.get(key):
                    p = Path(st[key])
                    frames.append((p if p.is_absolute() else base / p).resolve())
    return list(dict.fromkeys(frames))


def run_pipeline(video: Path, tap_log: Path, run_dir: Path, s: Settings,
                 progress: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Run all four stages in ``run_dir``; returns {"summary": ..., "grounded_flow": [...]}."""
    timings: dict[str, float] = {}

    def timed(name: str, fn):
        progress(name)
        t0 = time.time()
        try:
            return fn()
        except PipelineError:
            raise
        except Exception as exc:  # noqa: BLE001 - each stage raises its own error types
            raise PipelineError(f"{name} failed: {exc}") from exc
        finally:
            timings[name] = round(time.time() - t0, 1)

    calibration = Path(s.teach_calibration_dir)
    if not calibration.is_absolute():
        calibration = BACKEND_DIR / calibration
    cfg = StageConfig(calibration_dir=calibration, own_package=s.teach_recorder_package)
    stage1 = run_dir / "stage1"
    extraction = timed("stage1_tap_extraction", lambda: extract_taps_with_log(video, tap_log, stage1, cfg))

    flow_path = run_dir / "flow.json"
    flow = timed("stage2_flow_builder", lambda: build_flow(
        stage1 / "taps.json", stage1 / "events_report.json", flow_path))

    frames = frames_to_segment(flow_path)

    def stage3():
        stats, errors = segment_frames(frames, s)
        if errors:
            raise PipelineError(f"stage3_segmentation failed on {len(errors)}/{len(frames)} frame(s): "
                                + "; ".join(errors[:3]))
        return stats

    seg = timed("stage3_segmentation", stage3)
    grounded_path = run_dir / "grounded_flow.json"
    grounded = timed("stage4_grounding", lambda: ground_flow(flow_path, grounded_path, STAGES_DIR / "ground_flow"))

    return {
        "summary": {
            "taps_extracted": len(extraction.taps),
            "steps": len(flow.steps),
            "tap_steps": sum(1 for st in flow.steps if st["type"] == "tap"),
            "type_text_steps": sum(1 for st in flow.steps if st["type"] == "type_text"),
            "frames_segmented": len(seg),
            "grounded_contained": grounded.contained,
            "grounded_fallback_nearest": grounded.fallback_nearest,
            "segmentation_model": s.openrouter_seg_model,
            "segmentation_cost_usd": round(sum(x.get("cost") or 0 for x in seg), 5),
            "segmentation_retries": sum(x["attempts"] - 1 for x in seg),
            "timings_s": timings,
            "warnings": list(flow.warnings),
        },
        "grounded_flow": grounded.steps,
    }
