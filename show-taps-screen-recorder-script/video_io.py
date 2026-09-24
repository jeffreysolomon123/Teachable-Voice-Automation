"""Video reading with true per-frame timestamps (PyAV), safe for variable frame rate."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import av
import numpy as np

log = logging.getLogger(__name__)


class VideoError(Exception):
    """Raised when a video cannot be opened or decoded."""


@dataclass
class VideoInfo:
    """Basic properties of a video stream."""

    width: int
    height: int
    duration_ms: float | None


def _check_path(path: str | Path) -> Path:
    """Return ``path`` as a Path, raising VideoError if the file does not exist."""
    p = Path(path)
    if not p.is_file():
        raise VideoError(f"Video file not found: {p}")
    return p


class VideoReader:
    """Decode frames of a video with timestamps in milliseconds.

    Timestamps come from ``(pts - stream.start_time) * time_base`` and are never
    derived from an assumed constant FPS.
    """

    def __init__(self, path: str | Path) -> None:
        """Open ``path`` for reading."""
        self.path = _check_path(path)
        try:
            self._container = av.open(str(self.path))
        except av.FFmpegError as exc:
            raise VideoError(f"Cannot open video {self.path}: {exc}") from exc
        if not self._container.streams.video:
            raise VideoError(f"No video stream in {self.path}")
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"
        self._time_base = float(self._stream.time_base)
        self._start_pts = self._stream.start_time or 0

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying container."""
        self._container.close()

    @property
    def info(self) -> VideoInfo:
        """Width, height and (if known) duration of the video."""
        duration = None
        if self._stream.duration is not None:
            duration = self._stream.duration * self._time_base * 1000.0
        elif self._container.duration is not None:
            duration = self._container.duration / 1000.0
        return VideoInfo(self._stream.width, self._stream.height, duration)

    def _frame_ms(self, frame: av.VideoFrame) -> float:
        """Timestamp of ``frame`` in ms relative to the stream start."""
        if frame.pts is None:
            return float(frame.time or 0.0) * 1000.0
        return (frame.pts - self._start_pts) * self._time_base * 1000.0

    def _decode_from(self, start_ms: float) -> Iterator[tuple[float, av.VideoFrame]]:
        """Seek to the keyframe at or before ``start_ms`` and decode forward."""
        target_pts = int(max(start_ms, 0.0) / 1000.0 / self._time_base) + self._start_pts
        self._container.seek(target_pts, stream=self._stream, backward=True, any_frame=False)
        for frame in self._container.decode(self._stream):
            yield self._frame_ms(frame), frame

    def iter_frames(self) -> Iterator[tuple[float, np.ndarray]]:
        """Yield ``(timestamp_ms, bgr_frame)`` for every frame, from the start."""
        for ts, frame in self._decode_from(0.0):
            yield ts, frame.to_ndarray(format="bgr24")

    def frames_between(self, start_ms: float, end_ms: float) -> list[tuple[float, np.ndarray]]:
        """Return all frames with ``start_ms <= ts <= end_ms`` (small windows only)."""
        out: list[tuple[float, np.ndarray]] = []
        for ts, frame in self._decode_from(start_ms):
            if ts > end_ms + 0.5:
                break
            if ts >= start_ms - 0.5:
                out.append((ts, frame.to_ndarray(format="bgr24")))
        return out

    def frame_at(self, ts_ms: float) -> tuple[float, np.ndarray]:
        """Return the frame on screen at ``ts_ms``: the last frame with ts <= ts_ms.

        If ``ts_ms`` is before the first frame, the first frame is returned; if it is
        after the last frame, the last frame is returned.
        """
        best: tuple[float, av.VideoFrame] | None = None
        for ts, frame in self._decode_from(ts_ms):
            if ts > ts_ms + 0.5 and best is not None:
                break
            best = (ts, frame)
            if ts > ts_ms + 0.5:
                break
        if best is None:
            raise VideoError(f"Could not decode a frame at {ts_ms:.1f} ms in {self.path}")
        return best[0], best[1].to_ndarray(format="bgr24")


def get_frame_at(path: str | Path, ts_ms: float) -> tuple[float, np.ndarray]:
    """Convenience wrapper: open ``path`` and return the frame shown at ``ts_ms``."""
    with VideoReader(path) as reader:
        return reader.frame_at(ts_ms)
