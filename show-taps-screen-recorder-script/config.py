"""Central configuration: every tunable threshold lives in the Config dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """All thresholds used by the tap-extraction pipeline.

    Pixel distances marked "at 1080 px width" are scaled to the actual video width
    via :meth:`scaled`, so the same defaults work for 720p or 1440p recordings.
    """

    # --- Calibration -------------------------------------------------------
    calibration_dir: Path = field(default_factory=lambda: Path("calibration"))
    # Extra pixels kept around the circle when cropping calibration/template.png.
    template_margin_px: int = 4

    # --- Detection ---------------------------------------------------------
    # Frames (and the template) are downscaled by this factor before matching ...
    detect_scale: float = 0.5
    # ... but never so far that the circle radius drops below this many pixels
    # (the ring is thin; below ~20 px it aliases away and match scores collapse).
    min_detect_radius_px: float = 20.0
    # Template scales tried relative to the calibrated radius (+/-10 %).
    template_scales: tuple[float, ...] = (0.90, 1.00, 1.10)
    # Only a ring of the template is compared (inner edge as a fraction of the radius
    # to radius + 2 px): the show-taps disk is translucent, so its interior and the
    # crop corners are mostly whatever background was under it during calibration.
    template_ring_inner: float = 0.7
    # Minimum TM_CCOEFF_NORMED score for a template match to count as a circle.
    match_threshold: float = 0.80
    # Two detections closer than this fraction of the radius are the same circle (NMS).
    nms_distance_factor: float = 1.0
    # HoughCircles fallback: accumulator threshold and Canny high threshold.
    hough_param1: float = 100.0
    hough_param2: float = 20.0
    # Hough fallback: minimum fraction of the circle perimeter backed by edges (score).
    hough_min_score: float = 0.5

    # --- Tracking ----------------------------------------------------------
    # An event survives frames without a detection only while the gap since its
    # last detection is <= this. Consecutive frames that both contain a nearby
    # circle are always linked, because variable-frame-rate recordings emit no
    # frames while the screen is static (e.g. during a long press).
    max_gap_ms: float = 70.0
    # Max distance between consecutive points of one event (at 1080 px width).
    # Generous so a fast swipe is not broken into several fake taps.
    link_max_distance_px: float = 400.0

    # --- Classification ----------------------------------------------------
    # Max movement of a tap from its first point (at 1080 px width).
    tap_max_displacement_px: float = 25.0
    # Max duration of a tap; longer stationary touches are LONG_PRESS.
    tap_max_duration_ms: float = 400.0
    # Single-frame events whose best score is below this are NOISE.
    noise_min_score: float = 0.88
    # Width the pixel thresholds above are expressed in.
    reference_width: int = 1080

    # --- Output frames -----------------------------------------------------
    # The "before" frame is taken this long before the circle first appears ...
    before_offset_ms: float = 100.0
    # ... stepping further back frame by frame, up to this much, if a circle is visible.
    before_max_search_ms: float = 500.0
    # The "after" frame is taken this long after the finger lifted.
    after_offset_ms: float = 700.0

    # --- Debug video -------------------------------------------------------
    # Constant output FPS of debug/annotated.mp4 (source frames are held so timing is real).
    debug_fps: float = 30.0

    def scaled(self, value_px: float, video_width: int) -> float:
        """Scale a pixel threshold defined at ``reference_width`` to ``video_width``."""
        return value_px * video_width / self.reference_width
