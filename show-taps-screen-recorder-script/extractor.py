"""Orchestrate detection, tracking and classification, and write the outputs."""

from __future__ import annotations

import bisect
import json
import logging
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from candidates import build_candidates
from classifier import TAP, Classification, classify
from config import Config
from detector import CircleDetector, Detection, load_calibration
from locator import CircleLocator, Located
from log_io import load_log
from tracker import EventTracker, TouchEvent
from video_io import VideoReader

log = logging.getLogger(__name__)

LABEL_COLORS = {  # BGR
    "TAP": (0, 200, 0),
    "LONG_PRESS": (0, 165, 255),
    "SWIPE": (255, 120, 0),
    "MULTI_TOUCH": (255, 0, 255),
    "NOISE": (128, 128, 128),
}


class NoCirclesError(Exception):
    """Raised when no show-taps circle is detected anywhere in the video."""


@dataclass
class TapRecord:
    """One entry of taps.json."""

    id: int
    tap_x: int
    tap_y: int
    touch_ms: float
    duration_ms: float
    before: str
    touch: str
    after: str


@dataclass
class ExtractionResult:
    """Everything produced by :func:`extract_taps`."""

    taps: list[TapRecord]
    events_report: list[dict]
    out_dir: Path
    debug_video: Path | None = None
    frame_count: int = 0
    width: int = 0
    height: int = 0
    labels: dict[str, int] = field(default_factory=dict)


@dataclass
class Pass1Result:
    """Output of the detection pass (no frames are kept)."""

    timestamps: list[float]
    detections: list[list[Detection]]
    events: list[TouchEvent]
    width: int
    height: int


def detect_events(
    video_path: str | Path, detector_factory: Callable[[int], CircleDetector], cfg: Config
) -> tuple[Pass1Result, CircleDetector]:
    """Pass 1: run the detector on every frame and build events, without storing frames.

    ``detector_factory(width)`` must return a :class:`CircleDetector` for that frame width.
    """
    timestamps: list[float] = []
    detections: list[list[Detection]] = []
    with VideoReader(video_path) as reader:
        info = reader.info
        detector: CircleDetector = detector_factory(info.width)
        tracker = EventTracker(cfg, info.width)
        log.info(
            "Pass 1: detecting circles in %s (%dx%d, method=%s)",
            video_path, info.width, info.height, detector.method,
        )
        for i, (ts, frame) in enumerate(reader.iter_frames()):
            dets = detector.detect(frame)
            timestamps.append(ts)
            detections.append(dets)
            tracker.update(ts, dets)
            if (i + 1) % 300 == 0:
                log.info("  %d frames processed (t=%.1fs), %d events so far",
                         i + 1, ts / 1000, len(tracker.events))
    events = tracker.finish()
    log.info("Pass 1 done: %d frames, %d frames with circles, %d events",
             len(timestamps), sum(1 for d in detections if d), len(events))
    return Pass1Result(timestamps, detections, events, info.width, info.height), detector


def median_frame_interval(timestamps: list[float]) -> float:
    """Median spacing between consecutive frames in ms (33.3 if unknown)."""
    if len(timestamps) < 2:
        return 1000 / 30
    return float(np.median(np.diff(timestamps)))


def save_png(path: Path, frame_bgr: np.ndarray) -> None:
    """Save a BGR frame as a lossless PNG (fast compression; files are ~20% larger)."""
    Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)).save(path, format="PNG", compress_level=1)


def find_clean_before(
    reader: VideoReader,
    detector: CircleDetector,
    timestamps: list[float],
    touch_ms: float,
    cfg: Config,
) -> tuple[float, np.ndarray]:
    """Return a frame ~before_offset_ms before ``touch_ms`` with no circle in it.

    Starts at the frame shown at ``touch_ms - before_offset_ms`` and steps back one
    frame at a time (up to ``before_max_search_ms`` further) until the detector
    finds no circle. Falls back to the earliest frame checked, with a warning.
    """
    target = touch_ms - cfg.before_offset_ms
    earlier = [t for t in timestamps if t < touch_ms]
    if not earlier:
        log.warning("Tap at %.0f ms has no earlier frame; using the touch frame as 'before'", touch_ms)
        return reader.frame_at(touch_ms)
    idx = bisect.bisect_right(earlier, target) - 1
    if idx < 0:
        idx = 0  # tap starts less than before_offset_ms into the video
    limit = target - cfg.before_max_search_ms
    candidates = [earlier[idx]] + [t for t in reversed(earlier[:idx]) if t >= limit]
    window = dict(reader.frames_between(min(candidates), max(candidates)))
    fallback: tuple[float, np.ndarray] | None = None
    for t in candidates:
        frame = window.get(t)
        if frame is None:
            continue
        fallback = (t, frame)
        if not detector.detect(frame):
            return t, frame
    log.warning("No circle-free frame within %.0f ms before tap at %.0f ms; using earliest checked",
                cfg.before_offset_ms + cfg.before_max_search_ms, touch_ms)
    return fallback if fallback is not None else reader.frame_at(target)


def write_debug_video(
    video_path: str | Path,
    out_path: Path,
    p1: Pass1Result,
    labels: list[Classification],
    cfg: Config,
) -> None:
    """Write the video with detected circles outlined and the current event label overlaid.

    Output is constant ``cfg.debug_fps``; each source frame is held until the next
    one so playback timing matches the (variable-frame-rate) original.
    """
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), cfg.debug_fps, (p1.width, p1.height))
    if not writer.isOpened():
        raise RuntimeError(f"Cannot open VideoWriter for {out_path}")
    starts = [e.start_ms for e in p1.events]
    slot_ms = 1000.0 / cfg.debug_fps
    next_slot = 0
    prev: np.ndarray | None = None
    thick = max(2, p1.width // 360)
    font_scale = p1.width / 600
    log.info("Writing debug video %s", out_path)
    with VideoReader(video_path) as reader:
        for i, (ts, frame) in enumerate(reader.iter_frames()):
            if prev is not None:
                while next_slot * slot_ms < ts:
                    writer.write(prev)
                    next_slot += 1
            k = bisect.bisect_right(starts, ts) - 1
            label = None
            if k >= 0 and ts <= p1.events[k].end_ms:
                label = labels[k].label
            color = LABEL_COLORS.get(label or "", (0, 0, 255))
            for d in p1.detections[i] if i < len(p1.detections) else []:
                cv2.circle(frame, (round(d.x), round(d.y)), round(d.radius) + thick, color, thick)
            cv2.putText(frame, f"{ts / 1000:6.2f}s", (10, round(40 * font_scale)),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thick)
            if label:
                cv2.putText(frame, f"#{k + 1} {label}", (10, round(90 * font_scale)),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale * 1.3, color, thick + 1)
            prev = frame
        if prev is not None:
            writer.write(prev)
    writer.release()


def extract_taps(
    video_path: str | Path,
    out_dir: str | Path,
    cfg: Config | None = None,
    debug: bool = False,
) -> ExtractionResult:
    """Run the full pipeline on ``video_path`` and write outputs to ``out_dir``.

    Writes ``taps/tap_NNN_{before,touch,after}.png``, ``taps.json``,
    ``events_report.json`` and, with ``debug``, ``debug/annotated.mp4``.
    """
    cfg = cfg or Config()
    out = Path(out_dir)
    calibration = load_calibration(cfg.calibration_dir)

    p1, detector = detect_events(video_path, lambda w: CircleDetector(cfg, calibration, w), cfg)
    if not p1.events:
        raise NoCirclesError(
            f"No show-taps circles were found in {video_path}. Check that 'Show taps' was "
            "enabled while recording, and re-run calibrate.py on this video "
            "(python calibrate.py video.mp4 --time <seconds where a finger is down>)."
        )

    interval = median_frame_interval(p1.timestamps)
    labels = [classify(e, cfg, p1.width, interval) for e in p1.events]

    taps_dir = out / "taps"
    taps_dir.mkdir(parents=True, exist_ok=True)
    taps: list[TapRecord] = []
    report: list[dict] = []
    last_ts = p1.timestamps[-1]

    log.info("Pass 2: saving frames for %d taps", sum(1 for c in labels if c.label == TAP))
    with VideoReader(video_path) as reader:
        for n, (event, cls) in enumerate(zip(p1.events, labels), start=1):
            entry = {
                "event": n,
                "label": cls.label,
                "reason": cls.reason,
                "start_ms": round(event.start_ms, 1),
                "end_ms": round(event.end_ms, 1),
                "displacement_px": cls.displacement_px,
                "duration_ms": cls.duration_ms,
                "frames": event.frame_count,
                "max_simultaneous": event.max_simultaneous,
                "max_score": round(event.max_score, 3),
            }
            if cls.label == TAP:
                tap_id = len(taps) + 1
                names = {k: f"taps/tap_{tap_id:03d}_{k}.png" for k in ("before", "touch", "after")}
                _, before = find_clean_before(reader, detector, p1.timestamps, event.start_ms, cfg)
                _, touch = reader.frame_at(event.start_ms)
                _, after = reader.frame_at(min(event.end_ms + cfg.after_offset_ms, last_ts))
                for key, frame in (("before", before), ("touch", touch), ("after", after)):
                    save_png(out / names[key], frame)
                _, x, y = event.points[0]
                taps.append(TapRecord(tap_id, round(x), round(y), round(event.start_ms, 1),
                                      cls.duration_ms, names["before"], names["touch"], names["after"]))
                entry["tap_id"] = tap_id
            report.append(entry)

    (out / "taps.json").write_text(json.dumps([asdict(t) for t in taps], indent=2), encoding="utf-8")
    (out / "events_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for c in labels:
        counts[c.label] = counts.get(c.label, 0) + 1
    log.info("Events by label: %s", counts)

    debug_video = None
    if debug:
        (out / "debug").mkdir(parents=True, exist_ok=True)
        debug_video = out / "debug" / "annotated.mp4"
        write_debug_video(video_path, debug_video, p1, labels, cfg)

    return ExtractionResult(taps, report, out, debug_video, len(p1.timestamps), p1.width, p1.height, counts)


# --- log-guided mode (--log) ---------------------------------------------------------

NOT_FOUND_LABEL = "NOT_FOUND"
_CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}


@dataclass
class LoggedTapRecord(TapRecord):
    """One entry of taps.json in --log mode: TapRecord plus exact frame and log context."""

    touch_frame_index: int
    lift_ms: float
    match_score: float
    source: str  # click | keyboard | window_change
    confidence: str  # high | medium | low
    log_event_ms: float
    element: dict


class _PointProbe:
    """Stand-in detector for :func:`find_clean_before` that only looks at the tap point.

    Much faster than a whole-frame search. A static look-alike under the finger (a key
    glyph) can score fairly high, so the circle counts as present only within
    ``lift_drop`` of the score it had during the touch.
    """

    def __init__(self, locator: CircleLocator, loc: Located, cfg: Config) -> None:
        self.locator, self.x, self.y = locator, loc.x, loc.y
        self.floor = max(cfg.local_match_threshold, loc.score - cfg.lift_drop)

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """One detection if the circle is at the tap point in ``frame_bgr``, else none."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        hit = self.locator._best_in([gray], self.x, self.y, self.locator.radius * 0.5, self.floor)
        return [Detection(hit[0], hit[1], self.locator.radius, hit[2])] if hit else []


def _dedupe(located: list[Located], radius: float) -> list[tuple[Located, list[Located]]]:
    """Group results that found the same touch (same touch-down frame, same spot).

    Returns (kept, merged_into_it) pairs in time order; the higher-confidence result is kept.
    Results that were not found are never merged.
    """
    groups: list[tuple[Located, list[Located]]] = []
    for loc in sorted(located, key=lambda r: r.touch_ms):
        for i, (kept, merged) in enumerate(groups):
            if (loc.found and kept.found and loc.touch_ms == kept.touch_ms
                    and math.hypot(loc.x - kept.x, loc.y - kept.y) <= 2 * radius):
                if _CONFIDENCE_RANK[loc.candidate.confidence] > _CONFIDENCE_RANK[kept.candidate.confidence]:
                    groups[i] = (loc, merged + [kept])
                else:
                    merged.append(loc)
                break
        else:
            groups.append((loc, []))
    return groups


def _log_context(loc: Located) -> dict:
    """Log fields shared by taps.json and events_report.json entries."""
    c = loc.candidate
    return {
        "source": c.source,
        "confidence": c.confidence,
        "log_event_ms": round(c.log_event_ms, 1),
        "log_types": c.log_types,
        "element": c.element,
        "search_box": [round(v) for v in c.box],
        "search_window_ms": [round(v, 1) for v in c.window],
    }


def extract_taps_with_log(
    video_path: str | Path,
    log_path: str | Path,
    out_dir: str | Path,
    cfg: Config | None = None,
    debug: bool = False,
) -> ExtractionResult:
    """Log-guided extraction: search for the circle only where and when the tap log points.

    Writes the same files as :func:`extract_taps`. Every log candidate appears in
    ``events_report.json``, including those whose circle was not found in the video
    (``label: "NOT_FOUND"``) and those that found the same touch as another candidate
    (listed under the kept entry's ``merged_log_events``).
    """
    cfg = cfg or Config()
    out = Path(out_dir)
    calibration = load_calibration(cfg.calibration_dir)
    # The log already says a tap happened, so a short single-frame touch is not noise.
    cls_cfg = replace(cfg, noise_min_score=cfg.local_match_threshold)

    with VideoReader(video_path) as reader:
        info = reader.info
        timestamps = reader.timestamps()
        tap_log = load_log(log_path, video_path, info.width, info.height)
        candidates = build_candidates(tap_log, cfg)
        locator = CircleLocator(cfg, calibration, info.width, info.height)
        log.info("Locating %d log candidates in %s (%dx%d)", len(candidates), video_path, info.width, info.height)
        located = []
        for i, cand in enumerate(candidates, start=1):
            loc = locator.locate(reader, cand)
            located.append(loc)
            log.info("  [%d/%d] %-13s log %6.2fs -> %s", i, len(candidates), cand.source,
                     cand.log_event_ms / 1000,
                     f"touch {loc.touch_ms / 1000:.3f}s at ({loc.x:.0f}, {loc.y:.0f}) score {loc.score:.2f}"
                     if loc.found else loc.status)

        groups = _dedupe(located, locator.radius)
        interval = median_frame_interval(timestamps)
        last_ts = timestamps[-1]
        taps_dir = out / "taps"
        taps_dir.mkdir(parents=True, exist_ok=True)
        taps: list[LoggedTapRecord] = []
        report: list[dict] = []
        found_events: list[TouchEvent] = []
        found_labels: list[Classification] = []
        for n, (loc, merged) in enumerate(groups, start=1):
            entry: dict = {"event": n, **_log_context(loc)}
            if merged:
                entry["merged_log_events"] = [
                    {"source": m.candidate.source, "log_event_ms": round(m.candidate.log_event_ms, 1)}
                    for m in merged]
            if not loc.found:
                entry.update({
                    "label": NOT_FOUND_LABEL,
                    "status": loc.status,
                    "reason": loc.reason,
                    "start_ms": round(loc.touch_ms, 1),
                    "end_ms": round(loc.lift_ms, 1),
                    "tap_x": round(loc.x),
                    "tap_y": round(loc.y),
                })
                report.append(entry)
                continue
            event = loc.to_event()
            cls = classify(event, cls_cfg, info.width, interval)
            found_events.append(event)
            found_labels.append(cls)
            frame_index = bisect.bisect_left(timestamps, loc.touch_ms - 0.5)
            entry.update({
                "label": cls.label,
                "status": loc.status,
                "reason": cls.reason,
                "start_ms": round(loc.touch_ms, 1),
                "end_ms": round(loc.lift_ms, 1),
                "touch_frame_index": frame_index,
                "tap_x": round(loc.x),
                "tap_y": round(loc.y),
                "displacement_px": cls.displacement_px,
                "duration_ms": cls.duration_ms,
                "frames": event.frame_count,
                "match_score": round(loc.score, 3),
            })
            if cls.label == TAP:
                tap_id = len(taps) + 1
                names = {k: f"taps/tap_{tap_id:03d}_{k}.png" for k in ("before", "touch", "after")}
                probe = _PointProbe(locator, loc, cfg)
                _, before = find_clean_before(reader, probe, timestamps, loc.touch_ms, cfg)  # type: ignore[arg-type]
                _, touch = reader.frame_at(loc.touch_ms)
                _, after = reader.frame_at(min(loc.lift_ms + cfg.after_offset_ms, last_ts))
                for key, frame in (("before", before), ("touch", touch), ("after", after)):
                    save_png(out / names[key], frame)
                c = loc.candidate
                taps.append(LoggedTapRecord(
                    tap_id, round(loc.x), round(loc.y), round(loc.touch_ms, 1), cls.duration_ms,
                    names["before"], names["touch"], names["after"],
                    touch_frame_index=frame_index,
                    lift_ms=round(loc.lift_ms, 1),
                    match_score=round(loc.score, 3),
                    source=c.source,
                    confidence=c.confidence,
                    log_event_ms=round(c.log_event_ms, 1),
                    element=c.element,
                ))
                entry["tap_id"] = tap_id
            report.append(entry)

    (out / "taps.json").write_text(json.dumps([asdict(t) for t in taps], indent=2, ensure_ascii=False),
                                   encoding="utf-8")
    (out / "events_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    counts: dict[str, int] = {}
    for e in report:
        counts[e["label"]] = counts.get(e["label"], 0) + 1
    log.info("Events by label: %s", counts)

    debug_video = None
    if debug:
        (out / "debug").mkdir(parents=True, exist_ok=True)
        debug_video = out / "debug" / "annotated.mp4"
        order = sorted(range(len(found_events)), key=lambda i: found_events[i].start_ms)
        events = [found_events[i] for i in order]
        labels = [found_labels[i] for i in order]
        per_frame: list[list[Detection]] = [[] for _ in timestamps]
        index = {t: i for i, t in enumerate(timestamps)}
        for ev in events:
            for t, x, y in ev.points:
                i = index.get(t)
                if i is None:
                    i = min(bisect.bisect_left(timestamps, t), len(timestamps) - 1)
                per_frame[i].append(Detection(x, y, locator.radius, ev.max_score))
        p1 = Pass1Result(timestamps, per_frame, events, info.width, info.height)
        write_debug_video(video_path, debug_video, p1, labels, cfg)

    return ExtractionResult(taps, report, out, debug_video, len(timestamps), info.width, info.height, counts)
