"""Label touch events as TAP, LONG_PRESS, SWIPE, MULTI_TOUCH or NOISE."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import Config
from tracker import TouchEvent

TAP = "TAP"
LONG_PRESS = "LONG_PRESS"
SWIPE = "SWIPE"
MULTI_TOUCH = "MULTI_TOUCH"
NOISE = "NOISE"


@dataclass
class Classification:
    """Label for one event plus the measurements that justified it."""

    label: str
    reason: str
    displacement_px: float
    duration_ms: float


def displacement(event: TouchEvent) -> float:
    """Max distance (px) of any point of ``event`` from its first point."""
    _, x0, y0 = event.points[0]
    return max(math.hypot(x - x0, y - y0) for _, x, y in event.points)


def classify(
    event: TouchEvent, cfg: Config, frame_width: int, frame_interval_ms: float
) -> Classification:
    """Classify ``event``.

    ``frame_interval_ms`` (typically the median frame spacing) is added to the
    duration so single-frame events do not get zero duration.
    """
    disp = displacement(event)
    dur = event.end_ms - event.start_ms + frame_interval_ms
    max_disp = cfg.scaled(cfg.tap_max_displacement_px, frame_width)

    def result(label: str, reason: str) -> Classification:
        return Classification(label, reason, round(disp, 1), round(dur, 1))

    if event.frame_count == 1 and event.max_score < cfg.noise_min_score:
        return result(NOISE, f"single frame with score {event.max_score:.2f} < {cfg.noise_min_score:.2f}")
    if event.max_simultaneous > 1:
        return result(MULTI_TOUCH, f"{event.max_simultaneous} simultaneous circles")
    if disp > max_disp:
        return result(SWIPE, f"displacement {disp:.0f}px > {max_disp:.0f}px")
    if dur > cfg.tap_max_duration_ms:
        return result(
            LONG_PRESS,
            f"displacement {disp:.0f}px <= {max_disp:.0f}px, duration {dur:.0f}ms > {cfg.tap_max_duration_ms:.0f}ms",
        )
    return result(
        TAP,
        f"displacement {disp:.0f}px <= {max_disp:.0f}px, duration {dur:.0f}ms <= {cfg.tap_max_duration_ms:.0f}ms",
    )
