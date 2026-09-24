"""Find the Android "Show taps" circle in a frame."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config import Config

log = logging.getLogger(__name__)


class CalibrationError(Exception):
    """Raised when calibration files are missing or invalid."""


@dataclass
class Detection:
    """One circle found in a frame, in original video pixels."""

    x: float
    y: float
    radius: float
    score: float


@dataclass
class Calibration:
    """Circle appearance learned by calibrate.py."""

    radius_px: float
    frame_width: int
    frame_height: int
    template: np.ndarray | None  # grayscale crop, or None if only circle.json exists
    template_center: tuple[float, float]  # circle center inside the template crop


def load_calibration(calibration_dir: str | Path) -> Calibration:
    """Load ``circle.json`` and (optionally) ``template.png`` from ``calibration_dir``."""
    cdir = Path(calibration_dir)
    circle_json = cdir / "circle.json"
    if not circle_json.is_file():
        raise CalibrationError(
            f"Missing calibration file {circle_json}. Run first:\n"
            "  python calibrate.py your_video.mp4 --time <seconds where a finger is down>"
        )
    data = json.loads(circle_json.read_text(encoding="utf-8"))
    template = None
    template_png = cdir / "template.png"
    if template_png.is_file():
        template = cv2.imread(str(template_png), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise CalibrationError(f"Cannot read {template_png}; re-run calibrate.py")
    else:
        log.warning("No %s found; falling back to HoughCircles", template_png)
    r = float(data["radius_px"])
    center = data.get("template_center", [r, r])
    return Calibration(
        radius_px=r,
        frame_width=int(data["frame_width"]),
        frame_height=int(data["frame_height"]),
        template=template,
        template_center=(float(center[0]), float(center[1])),
    )


def non_max_suppression(dets: list[Detection], min_distance: float) -> list[Detection]:
    """Keep the highest-scoring detections, dropping any closer than ``min_distance`` to a kept one."""
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: d.score, reverse=True):
        if all(np.hypot(d.x - k.x, d.y - k.y) >= min_distance for k in kept):
            kept.append(d)
    return kept


@dataclass
class _ScaledTemplate:
    """A template prepared for masked normalised cross-correlation at one scale."""

    scale: float
    zero_mean: np.ndarray  # (template - masked mean) * mask, float32
    norm: float  # L2 norm of zero_mean
    mask: np.ndarray  # float32 0/1
    count: float  # number of mask pixels
    cx: float  # circle center inside the template
    cy: float

    @classmethod
    def build(cls, scale: float, tmpl: np.ndarray, mask: np.ndarray, cx: float, cy: float) -> "_ScaledTemplate":
        """Precompute the zero-mean masked template."""
        t = tmpl.astype(np.float32)
        n = float(mask.sum())
        zero_mean = (t - float((t * mask).sum()) / n) * mask
        return cls(scale, zero_mean, float(np.sqrt((zero_mean**2).sum())), mask, n, cx, cy)


# Image windows whose masked standard deviation is below this (grey levels) are
# treated as flat: correlation there is numerically meaningless.
_MIN_WINDOW_STD = 2.0


def masked_ccoeff_normed(img: np.ndarray, img_sq: np.ndarray, t: _ScaledTemplate) -> np.ndarray:
    """TM_CCOEFF_NORMED restricted to ``t.mask``.

    Equivalent to ``cv2.matchTemplate(..., TM_CCOEFF_NORMED, mask=...)`` but about
    twice as fast, and returns 0 (instead of inf/nan) on flat image regions.
    ``img`` is float32 grayscale and ``img_sq`` is ``img * img``.
    """
    num = cv2.matchTemplate(img, t.zero_mean, cv2.TM_CCORR)
    s1 = cv2.matchTemplate(img, t.mask, cv2.TM_CCORR)
    s2 = cv2.matchTemplate(img_sq, t.mask, cv2.TM_CCORR)
    var = s2 - s1 * s1 / t.count
    out = np.zeros_like(num)
    ok = var > t.count * _MIN_WINDOW_STD**2
    out[ok] = num[ok] / (np.sqrt(var[ok]) * t.norm)
    return out


class CircleDetector:
    """Detect show-taps circles with multi-scale template matching (or Hough fallback)."""

    def __init__(self, cfg: Config, calibration: Calibration, frame_width: int) -> None:
        """Prepare scaled templates for a video whose width is ``frame_width``."""
        self.cfg = cfg
        self.calib = calibration
        # If the video resolution differs from the calibration video, rescale everything.
        self.res_scale = frame_width / calibration.frame_width
        self.radius = calibration.radius_px * self.res_scale
        # Downscale for speed, but keep the circle at least min_detect_radius_px in size.
        self.scale = min(1.0, max(cfg.detect_scale, cfg.min_detect_radius_px / self.radius))
        self._templates: list[_ScaledTemplate] = []
        if calibration.template is not None:
            h, w = calibration.template.shape
            for s in cfg.template_scales:
                f = s * self.res_scale * self.scale
                tw, th = max(3, round(w * f)), max(3, round(h * f))
                tmpl = cv2.resize(calibration.template, (tw, th), interpolation=cv2.INTER_AREA)
                cx = calibration.template_center[0] * tw / w
                cy = calibration.template_center[1] * th / h
                r = calibration.radius_px * f
                yy, xx = np.mgrid[:th, :tw]
                dist = np.hypot(xx - cx, yy - cy)
                ring = ((dist >= r * cfg.template_ring_inner) & (dist <= r + 2)).astype(np.float32)
                self._templates.append(_ScaledTemplate.build(s, tmpl, ring, cx, cy))

    @property
    def method(self) -> str:
        """Name of the detection method in use."""
        return "template" if self._templates else "hough"

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        """Return all circles in ``frame_bgr`` (original-resolution coordinates)."""
        k = self.scale
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k != 1 else gray
        dets = self._detect_template(small) if self._templates else self._detect_hough(small)
        return non_max_suppression(dets, self.cfg.nms_distance_factor * self.radius)

    def _detect_template(self, small: np.ndarray) -> list[Detection]:
        """Multi-scale masked TM_CCOEFF_NORMED matching; local maxima above the threshold."""
        k = self.scale
        img = small.astype(np.float32)
        img_sq = img * img
        dets: list[Detection] = []
        for t in self._templates:
            th, tw = t.zero_mean.shape
            if th > small.shape[0] or tw > small.shape[1]:
                continue
            res = masked_ccoeff_normed(img, img_sq, t)
            if res.max() < self.cfg.match_threshold:
                continue
            s, cx, cy = t.scale, t.cx, t.cy
            # Local maxima: equal to the max in a neighbourhood about one radius wide.
            ksize = max(3, int(self.radius * k) | 1)
            peaks = (res >= cv2.dilate(res, np.ones((ksize, ksize), np.uint8))) & (
                res >= self.cfg.match_threshold
            )
            for py, px in zip(*np.nonzero(peaks)):
                dets.append(
                    Detection(
                        x=(px + cx) / k,
                        y=(py + cy) / k,
                        radius=self.radius * s,
                        score=float(min(res[py, px], 1.0)),
                    )
                )
        return dets

    def _detect_hough(self, small: np.ndarray) -> list[Detection]:
        """HoughCircles with a tight radius range; score = edge support along the perimeter."""
        k = self.scale
        r = self.radius * k
        blurred = cv2.GaussianBlur(small, (5, 5), 1.2)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=max(1.0, r),
            param1=self.cfg.hough_param1,
            param2=self.cfg.hough_param2,
            minRadius=max(1, int(r * 0.9)),
            maxRadius=max(2, int(np.ceil(r * 1.1))),
        )
        if circles is None:
            return []
        edges = cv2.Canny(blurred, self.cfg.hough_param1 / 2, self.cfg.hough_param1)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        dets: list[Detection] = []
        for cx, cy, cr in circles[0]:
            xs = np.clip((cx + cr * np.cos(angles)).astype(int), 0, edges.shape[1] - 1)
            ys = np.clip((cy + cr * np.sin(angles)).astype(int), 0, edges.shape[0] - 1)
            score = float(np.mean(edges[ys, xs] > 0))
            if score >= self.cfg.hough_min_score:
                dets.append(Detection(x=cx / k, y=cy / k, radius=cr / k, score=score))
        return dets
