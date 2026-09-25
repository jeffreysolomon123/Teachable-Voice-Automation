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


# Pixel formats whose first plane is the 8-bit luma image (full range for the "j" variants).
_LUMA_FIRST = {"yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "nv12", "nv21"}


def _to_array(frame: av.VideoFrame, fmt: str) -> np.ndarray:
    """Convert ``frame`` to a numpy array; "gray" copies the luma plane directly when possible."""
    if fmt == "gray" and frame.format.name in _LUMA_FIRST:
        plane = frame.planes[0]
        return np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)[:, :frame.width].copy()
    return frame.to_ndarray(format=fmt)


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

    def frames_between(
        self, start_ms: float, end_ms: float, fmt: str = "bgr24"
    ) -> list[tuple[float, np.ndarray]]:
        """Return all frames with ``start_ms <= ts <= end_ms`` (small windows only).

        ``fmt="gray"`` returns the luma plane only, which is much cheaper to convert.
        """
        out: list[tuple[float, np.ndarray]] = []
        for ts, frame in self._decode_from(start_ms):
            if ts > end_ms + 0.5:
                break
            if ts >= start_ms - 0.5:
                out.append((ts, _to_array(frame, fmt)))
        return out

    def timestamps(self) -> list[float]:
        """Timestamps (ms) of every frame, in order, read from packets without decoding."""
        ts: list[float] = []
        self._container.seek(self._start_pts, stream=self._stream, backward=True, any_frame=False)
        for packet in self._container.demux(self._stream):
            if packet.pts is not None:
                ts.append((packet.pts - self._start_pts) * self._time_base * 1000.0)
        return sorted(ts)

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
