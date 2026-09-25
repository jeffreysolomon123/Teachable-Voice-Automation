"""Group per-frame circle detections into touch events."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import Config
from detector import Detection


@dataclass
class TouchEvent:
    """One continuous touch: consecutive frames showing a circle."""

    start_ms: float
    end_ms: float
    points: list[tuple[float, float, float]] = field(default_factory=list)  # (ts_ms, x, y)
    max_simultaneous: int = 1
    max_score: float = 0.0

    @property
    def frame_count(self) -> int:
        """Number of frames in which the circle was seen."""
        return len(self.points)


class EventTracker:
    """Streaming tracker: feed it every frame in order, then call :meth:`finish`.

    Only one event is open at a time. If a frame shows several circles, they are
    folded into the open event (tracked via ``max_simultaneous``) and the circle
    nearest the previous point continues the path.
    """

    def __init__(self, cfg: Config, frame_width: int) -> None:
        """Create a tracker for a video of width ``frame_width``."""
        self.max_gap_ms = cfg.max_gap_ms
        self.link_dist = cfg.scaled(cfg.link_max_distance_px, frame_width)
        self.events: list[TouchEvent] = []
        self._open: TouchEvent | None = None

    def update(self, ts_ms: float, detections: list[Detection]) -> None:
        """Process one frame's detections (an empty list means no circle in that frame)."""
        ev = self._open
        if not detections:
            if ev is not None and ts_ms - ev.end_ms > self.max_gap_ms:
                self._close()
            return

        if ev is not None:
            _, lx, ly = ev.points[-1]
            nearest = min(detections, key=lambda d: math.hypot(d.x - lx, d.y - ly))
            if math.hypot(nearest.x - lx, nearest.y - ly) <= self.link_dist:
                ev.points.append((ts_ms, nearest.x, nearest.y))
                ev.end_ms = ts_ms
                ev.max_simultaneous = max(ev.max_simultaneous, len(detections))
                ev.max_score = max(ev.max_score, max(d.score for d in detections))
                return
            self._close()

        first = max(detections, key=lambda d: d.score)
        self._open = TouchEvent(
            start_ms=ts_ms,
            end_ms=ts_ms,
            points=[(ts_ms, first.x, first.y)],
            max_simultaneous=len(detections),
            max_score=max(d.score for d in detections),
        )

    def _close(self) -> None:
        """Move the open event to the finished list."""
        if self._open is not None:
            self.events.append(self._open)
            self._open = None

    def finish(self) -> list[TouchEvent]:
        """Close any open event and return all events in time order."""
        self._close()
        return self.events


def build_events(
    frames: list[tuple[float, list[Detection]]], cfg: Config, frame_width: int
) -> list[TouchEvent]:
    """Group ``(timestamp_ms, detections)`` for every frame (in order) into events."""
    tracker = EventTracker(cfg, frame_width)
    for ts, dets in frames:
        tracker.update(ts, dets)
    return tracker.finish()
