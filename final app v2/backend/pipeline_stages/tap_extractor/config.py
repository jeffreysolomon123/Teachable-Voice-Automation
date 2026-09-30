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

    # --- Log-guided mode (--log) ---------------------------------------------
    # Package of the recorder app itself; its events are ignored (except the Stop tap).
    own_package: str = "com.example.taprecorder"
    # Padding added around an element's box before searching it (at 1080 px width).
    box_padding_px: float = 60.0
    # Search windows relative to the log event time [t - before, t + after] per source.
    click_window_ms: tuple[float, float] = (800.0, 150.0)
    keyboard_window_ms: tuple[float, float] = (600.0, 150.0)
    window_change_window_ms: tuple[float, float] = (1000.0, 0.0)
    # A click fires on finger-up, so the touch of a VIEW_CLICKED ends at most this long
    # after the event (clock-anchor slack); this cuts off the circle's fade-out.
    click_lift_slack_ms: float = 50.0
    # A key's VIEW_TEXT_CHANGED fires on key-up too, so a keystroke's touch is cut this long
    # after its event. Needed because key glyphs look like the circle to the template (they
    # score ~0.67), so the trace alone would run on past the lift.
    keyboard_lift_slack_ms: float = 50.0
    # A new screen only becomes a candidate if no click/key candidate is this close before it.
    window_change_click_gap_ms: float = 1000.0
    # Candidates closer than this in time that point to the same place are merged.
    merge_ms: float = 150.0
    # Minimum masked-correlation score of the circle (its best frame) to accept a touch.
    # Low on purpose: the template is learned on one background and scores ~0.5 on light
    # ones; the appearance test below is what rejects look-alikes.
    local_match_threshold: float = 0.45
    # The finger has lifted once the score falls this far below its highest value so far
    # (the circle fades out after lift instead of vanishing). The touch-down frame itself
    # may score this much below local_match_threshold (busy backgrounds lower the score).
    lift_drop: float = 0.15
    # Touch-down = the frame where a circle *appears*: at least appear_min_inside of the
    # pixels inside the disk and along its edge changed by more than appear_pixel_delta grey
    # levels since the previous frame, while at most appear_max_outside of a ring just
    # outside it (1.15-2.2 radii) did. Screen transitions, animations and moving or scaling
    # icons change the surroundings too; a static UI feature (a key glyph under the
    # circle) does not change at all.
    appear_min_inside: float = 0.6
    appear_max_outside: float = 0.2
    appear_pixel_delta: int = 8
    # ... and the template score must jump by at least this much in that frame (a fading
    # circle also changes its whole disk, but its score falls).
    onset_min_jump: float = 0.10
    # At most this many coarse appearance spots per frame are checked (best first).
    max_onsets_per_frame: int = 8
    # While following a moving touch (swipe), the circle may move at most this far between
    # frames (at 1080 px width), and its score there must rise at least local_min_rise over
    # the previous frame, so the trace cannot jump onto a static look-alike.
    walk_max_step_px: float = 120.0
    local_min_rise: float = 0.20
    # Frames decoded after the search window so the lift can be traced past its end.
    walk_margin_ms: float = 600.0
    # Onsets are first screened on frame differences at 1/change_downscale size (the same
    # appearance test, on a coarse grid); only spots that pass are template-matched.
    change_downscale: int = 4

    def scaled(self, value_px: float, video_width: int) -> float:
        """Scale a pixel threshold defined at ``reference_width`` to ``video_width``."""
        return value_px * video_width / self.reference_width
