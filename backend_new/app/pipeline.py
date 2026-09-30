"""Tap-to-flow pipeline: video + tap log -> grounded_flow.json.

  stage 1  tap_extractor   (unchanged copy)  video + log -> taps.json, events_report.json, taps/*.png
  stage 2  flow_builder    (unchanged copy)  -> flow.json + flow_frames/*.png
  stage 3  app.segmentation (NEW: one OpenRouter vision call per frame, frames in parallel)
                                             -> flow_frames/segment_out/*_combined.json, *_ocr.json
  stage 4  ground_flow     (unchanged copy)  -> grounded_flow.json

Stage 4 reads stage 3's results from its segment_out/ cache, so with every frame pre-segmented
it never runs its own (YOLO/EasyOCR) segmentation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import segmentation
from .settings import ROOT, settings

STAGES_DIR = ROOT / "pipeline_stages"
# The copied stages use flat imports (e.g. "from config import Config"), as in tap-to-flow-pipeline.
for _d in ("tap_extractor", "flow_builder", "ground_flow"):
    if str(STAGES_DIR / _d) not in sys.path:
        sys.path.insert(0, str(STAGES_DIR / _d))

from config import Config  # noqa: E402  (stage 1)
from extractor import extract_taps_with_log  # noqa: E402
from flow_builder import build_flow  # noqa: E402  (stage 2)
from ground_flow import ground_flow  # noqa: E402  (stage 4)


class PipelineError(Exception):
    """A stage failed; the message names the stage."""


def frames_to_segment(flow_path: Path) -> list[Path]:
    """Before and after frames of every tap step (stage 4 grounds on before, OCRs both)."""
    base = flow_path.parent
    steps = json.loads(flow_path.read_text(encoding="utf-8"))
    frames: list[Path] = []
    for s in steps:
        if s.get("type") != "tap":
            continue
        for key in ("before_frame", "after_frame"):
            if s.get(key):
                p = Path(s[key])
                frames.append((p if p.is_absolute() else base / p).resolve())
    return list(dict.fromkeys(frames))


def run_pipeline(video: Path, tap_log: Path, run_dir: Path,
                 progress: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    """Run all four stages in ``run_dir``. Returns a summary with the grounded flow."""
    timings: dict[str, float] = {}

    def timed(name: str, fn):
        progress(name)
        t0 = time.time()
        try:
            return fn()
        except PipelineError:
            raise
        except Exception as exc:  # noqa: BLE001 - stages raise their own error types
            raise PipelineError(f"{name} failed: {exc}") from exc
        finally:
            timings[name] = round(time.time() - t0, 1)

    stage1_dir = run_dir / "stage1"
    extraction = timed("stage1_tap_extraction", lambda: extract_taps_with_log(
        video, tap_log, stage1_dir, Config(calibration_dir=settings.calibration_dir)))

    flow_path = run_dir / "flow.json"
    flow = timed("stage2_flow_builder", lambda: build_flow(
        stage1_dir / "taps.json", stage1_dir / "events_report.json", flow_path))

    frames = frames_to_segment(flow_path)

    def stage3():
        stats, errors = segmentation.segment_frames(frames)
        if errors:
            raise PipelineError(f"stage3_segmentation failed on {len(errors)}/{len(frames)} frame(s): "
                                + "; ".join(errors[:3]))
        return stats

    seg_stats = timed("stage3_segmentation", stage3)

    grounded_path = run_dir / "grounded_flow.json"
    grounded = timed("stage4_grounding", lambda: ground_flow(
        flow_path, grounded_path, STAGES_DIR / "ground_flow"))

    cost = sum((s.get("usage") or {}).get("cost") or 0 for s in seg_stats)
    return {
        "summary": {
            "taps_extracted": len(extraction.taps),
            "steps": len(flow.steps),
            "tap_steps": sum(1 for s in flow.steps if s["type"] == "tap"),
            "type_text_steps": sum(1 for s in flow.steps if s["type"] == "type_text"),
            "frames_segmented": len(seg_stats),
            "grounded_contained": grounded.contained,
            "grounded_fallback_nearest": grounded.fallback_nearest,
            "review_flags": [vars(f) for f in grounded.review_flags],
            "segmentation_model": settings.seg_model,
            "segmentation_cost_usd": round(cost, 5),
            "segmentation_retries": sum(s["attempts"] - 1 for s in seg_stats),
            "timings_s": timings,
            "warnings": list(flow.warnings),
        },
        "grounded_flow": grounded.steps,
    }
