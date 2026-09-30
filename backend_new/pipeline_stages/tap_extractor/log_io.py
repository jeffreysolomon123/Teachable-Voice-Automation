"""Read the accessibility log written by the TapScreenRecorder app (schemaVersion 1)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 1

# (left, top, right, bottom) in video pixels.
Box = tuple[float, float, float, float]


class LogError(Exception):
    """Raised when the tap log is missing, malformed or unusable."""


@dataclass
class TapLog:
    """A loaded tap log, with coordinates already mapped to the video's pixel grid."""

    path: Path
    events: list[dict]  # sorted by videoTimeMs
    keyboard_snapshots: list[dict]  # sorted by videoTimeMs
    log_width: int
    log_height: int
    video_width: int
    video_height: int

    @property
    def scale_x(self) -> float:
        """Factor from log (screen) x to video x."""
        return self.video_width / self.log_width

    @property
    def scale_y(self) -> float:
        """Factor from log (screen) y to video y."""
        return self.video_height / self.log_height

    def box(self, bounds: dict | None) -> Box | None:
        """Convert a log ``bounds`` dict to a video-pixel box, or None if missing/empty."""
        if not bounds:
            return None
        l, t, r, b = (float(bounds[k]) for k in ("left", "top", "right", "bottom"))
        if r <= l or b <= t:
            return None
        return (l * self.scale_x, t * self.scale_y, r * self.scale_x, b * self.scale_y)

    @property
    def screen(self) -> Box:
        """The whole video frame as a box."""
        return (0.0, 0.0, float(self.video_width), float(self.video_height))


def load_log(path: str | Path, video_path: str | Path, video_width: int, video_height: int) -> TapLog:
    """Load and validate ``path`` for the video at ``video_path`` of the given size.

    Raises LogError for a missing file, invalid JSON, an unsupported schema, a logger
    that was off during recording, or a log with no events. Warns (but continues) if
    the log names a different video file. If the log's screen size differs from the
    video (e.g. a re-compressed copy), coordinates are scaled to the video.
    """
    p = Path(path)
    if not p.is_file():
        raise LogError(f"Tap log not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LogError(f"Tap log {p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LogError(f"Tap log {p} must be a JSON object")

    schema = data.get("schemaVersion")
    if schema != SUPPORTED_SCHEMA:
        raise LogError(f"Tap log {p} has schemaVersion {schema!r}; only {SUPPORTED_SCHEMA} is supported")

    logger = data.get("logger") or {}
    if logger.get("enabledAtStart") is False:
        raise LogError(
            f"Tap log {p}: the tap logger was OFF when recording started (logger.enabledAtStart == false). "
            "Enable it in the TapScreenRecorder app and record again, or run without --log."
        )
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise LogError(f"Tap log {p} contains no events; run without --log to use the video only.")
    if logger.get("droppedEvents"):
        log.warning("Tap log dropped %d events (log size cap reached)", logger["droppedEvents"])

    video = data.get("video") or {}
    name = video.get("fileName")
    if name and name != Path(video_path).name:
        log.warning("Tap log is for %s but the video is %s; make sure they are from the same recording",
                    name, Path(video_path).name)
    log_w = int(video.get("width") or video_width)
    log_h = int(video.get("height") or video_height)
    if (log_w, log_h) != (video_width, video_height):
        log.warning("Tap log screen is %dx%d but the video is %dx%d; scaling log coordinates",
                    log_w, log_h, video_width, video_height)
        if abs(log_w / log_h - video_width / video_height) > 0.02:
            log.warning("Aspect ratios differ; the video may be cropped or rotated and positions may be off")

    snaps = [s for s in data.get("keyboardSnapshots") or [] if isinstance(s, dict)]
    return TapLog(
        path=p,
        events=sorted((e for e in events if isinstance(e, dict) and "videoTimeMs" in e),
                      key=lambda e: e["videoTimeMs"]),
        keyboard_snapshots=sorted(snaps, key=lambda s: s.get("videoTimeMs", 0)),
        log_width=log_w,
        log_height=log_h,
        video_width=video_width,
        video_height=video_height,
    )
