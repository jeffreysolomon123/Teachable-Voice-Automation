"""Find the show-taps circle for one log candidate: exact touch-down frame and point.

Per candidate:

1. **Onsets.** A touch-down is the frame where a circle *appears* inside the search box:
   nearly every pixel inside the disk changed since the previous frame, almost none in a
   ring around it, and the circle template matches there (score >= ``local_match_threshold
   - lift_drop``, at least ``onset_min_jump`` higher than in the previous frame). That
   rejects static look-alikes (a key glyph can score ~0.77 against the template but never
   changes), animations and screen transitions (the surroundings change too), and a circle
   fading out after lift (its score falls). The test is screened on a coarse grid first,
   so only a few spots per frame are template-matched.
2. **Trace** each onset forwards at full resolution (same spot first, else where a
   moving finger went) until the score falls ``lift_drop`` below its best: the circle
   fades after lift. The touch counts only if its best score reaches ``local_match_threshold``.
3. Keep the **latest** touch-down in the window: the log event comes from the last
   gesture before it (an earlier swipe or tap in the same box is a different gesture).
   For a click the log also bounds the lift: the click fires on finger-up.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from candidates import Candidate
from config import Config
from detector import Calibration, CalibrationError, CircleDetector
from log_io import Box
from tracker import TouchEvent
from video_io import VideoReader

log = logging.getLogger(__name__)

FOUND = "found"
NOT_FOUND = "not_found_in_video"


@dataclass
class Located:
    """Result of locating one candidate in the video."""

    candidate: Candidate
    status: str  # FOUND or NOT_FOUND
    touch_ms: float  # touch-down frame time (log time if not found)
    lift_ms: float  # last frame with the finger down (log time if not found)
    x: float  # touch point, video pixels (search-box centre if not found)
    y: float
    score: float = 0.0  # best circle score along the trace
    points: list[tuple[float, float, float]] = field(default_factory=list)  # (ts_ms, x, y)
    reason: str = ""

    @property
    def found(self) -> bool:
        """True if the circle was found in the video."""
        return self.status == FOUND

    def to_event(self) -> TouchEvent:
        """The traced touch as a TouchEvent, for the classifier."""
        return TouchEvent(self.touch_ms, self.lift_ms, list(self.points), 1, self.score)


@dataclass
class _Onset:
    """A circle appearing: frame index, centre and template score."""

    frame: int
    x: float
    y: float
    score: float


@dataclass
class _Trace:
    """An onset followed until lift."""

    onset: _Onset
    lift: int  # frame index of the last frame with the circle
    points: list[tuple[float, float, float]]  # (ts_ms, x, y)
    best: float


@dataclass
class _Maps:
    """Score maps of one region for several frames; index (v, u) is centre (x0 + u, y0 + v)."""

    scores: np.ndarray  # (frames, h, w) float32, -1 where no valid centre
    x0: int
    y0: int


class CircleLocator:
    """Locate candidates in one video (see module docstring)."""

    def __init__(self, cfg: Config, calibration: Calibration, width: int, height: int) -> None:
        """Prepare a full-resolution detector for a ``width`` x ``height`` video."""
        if calibration.template is None:
            raise CalibrationError("--log mode needs calibration/template.png; re-run calibrate.py")
        self.cfg = cfg
        self.width, self.height = width, height
        self.det = CircleDetector(replace(cfg, detect_scale=1.0), calibration, width)
        self.radius = self.det.radius
        self.walk_step = cfg.scaled(cfg.walk_max_step_px, width)
        self.onset_floor = cfg.local_match_threshold - cfg.lift_drop

        # Full-resolution appearance test: disk, disk edge and rings in a (2m+1)^2 patch.
        # A real circle's change stops exactly at its radius: its edge changes, a ring just
        # outside it does not. An icon or card moving/scaling changes both.
        m = self._patch_half = math.ceil(self.radius * 2.2) + 1
        yy, xx = np.mgrid[-m:m + 1, -m:m + 1]
        dist = np.hypot(xx, yy)
        self._inside = dist <= self.radius * 0.85
        self._edge = (dist > self.radius * 0.85) & (dist <= self.radius * 1.05)
        self._outside = (dist >= self.radius * 1.4) & (dist <= self.radius * 2.2)
        self._outside_tight = (dist >= self.radius * 1.15) & (dist <= self.radius * 2.2)

        # Coarse appearance test at 1/d size, with squares instead of disks (box filters are
        # O(1) per pixel): a square inside the disk, and the frame between squares of
        # side 2.8 r and 4.4 r as the ring. Only a pre-screen; the full test runs after.
        d = cfg.change_downscale
        self._s_in = max(1, round(self.radius * 1.2 / d))
        self._s_mid = max(self._s_in + 2, round(self.radius * 2.8 / d))
        self._s_out = max(self._s_mid + 2, round(self.radius * 4.4 / d))
        n = max(3, round(self.radius / d) | 1)
        self._nms = np.ones((n, n), np.uint8)

    # --- helpers --------------------------------------------------------------------

    def _maps(self, imgs: list[np.ndarray], box: tuple[float, float, float, float]) -> _Maps:
        """Full-resolution score maps for circle centres inside ``box`` in each grayscale frame."""
        ext = self.det.template_extent
        l, t, r, b = box
        x0, y0 = max(0, math.floor(l) - ext), max(0, math.floor(t) - ext)
        x1, y1 = min(self.width, math.ceil(r) + ext + 1), min(self.height, math.ceil(b) + ext + 1)
        scores = np.stack([self.det.score_map(img[y0:y1, x0:x1]) for img in imgs])
        h, w = scores.shape[1:]
        xs, ys = x0 + np.arange(w), y0 + np.arange(h)
        scores[:, :, (xs < l) | (xs > r)] = -1.0
        scores[:, (ys < t) | (ys > b), :] = -1.0
        return _Maps(scores, x0, y0)

    def _best_in(self, imgs: list[np.ndarray], x: float, y: float, reach: float, floor: float,
                 min_rise: float | None = None) -> tuple[float, float, float] | None:
        """Best circle within ``reach`` of (x, y) in ``imgs[-1]`` scoring >= ``floor``.

        With ``min_rise``, ``imgs`` is (previous, current) and the score must also have
        risen that much since the previous frame (the circle moved there).
        """
        box = (max(0.0, x - reach), max(0.0, y - reach),
               min(self.width - 1.0, x + reach), min(self.height - 1.0, y + reach))
        maps = self._maps(imgs, box)
        now = maps.scores[-1]
        ok = now >= floor
        if min_rise is not None:
            ok &= now - maps.scores[0] >= min_rise
        if not ok.any():
            return None
        v, u = np.unravel_index(np.argmax(np.where(ok, now, -np.inf)), now.shape)
        return float(maps.x0 + u), float(maps.y0 + v), float(now[v, u])

    def appears(self, prev: np.ndarray, cur: np.ndarray, x: float, y: float, strict: bool = True) -> bool:
        """True if the change from ``prev`` to ``cur`` around (x, y) looks like a circle appearing.

        ``strict`` also checks the disk edge and a ring starting just outside it, which
        needs (x, y) within a few pixels of the true centre; the lenient test tolerates a
        coarse centre.
        """
        cfg = self.cfg
        m = self._patch_half
        cx, cy = round(x), round(y)
        x0, y0 = max(0, cx - m), max(0, cy - m)
        x1, y1 = min(self.width, cx + m + 1), min(self.height, cy + m + 1)
        changed = cv2.absdiff(cur[y0:y1, x0:x1], prev[y0:y1, x0:x1]) > cfg.appear_pixel_delta
        mx0, my0 = x0 - (cx - m), y0 - (cy - m)

        def frac(mask: np.ndarray, empty: float) -> float:
            sub = mask[my0:my0 + changed.shape[0], mx0:mx0 + changed.shape[1]]
            return float(changed[sub].mean()) if sub.any() else empty

        if frac(self._inside, 0.0) < cfg.appear_min_inside:
            return False
        if not strict:
            return frac(self._outside, 0.0) <= cfg.appear_max_outside
        return (frac(self._edge, 0.0) >= cfg.appear_min_inside
                and frac(self._outside_tight, 0.0) <= cfg.appear_max_outside)

    # --- steps --------------------------------------------------------------------------

    def _onsets(self, imgs: list[np.ndarray], in_window: np.ndarray, box: Box) -> list[_Onset]:
        """Every circle that appears inside ``box`` in an in-window frame, in time order."""
        cfg = self.cfg
        d, m = cfg.change_downscale, self._patch_half
        rx0, ry0 = max(0, math.floor(box[0]) - m), max(0, math.floor(box[1]) - m)
        rx1, ry1 = min(self.width, math.ceil(box[2]) + m), min(self.height, math.ceil(box[3]) + m)
        small = [cv2.resize(img[ry0:ry1, rx0:rx1], None, fx=1 / d, fy=1 / d, interpolation=cv2.INTER_AREA)
                 for img in imgs]
        onsets: list[_Onset] = []
        for j in range(1, len(imgs)):
            if not in_window[j]:
                continue
            changed = (cv2.absdiff(small[j], small[j - 1]) > cfg.appear_pixel_delta).astype(np.float32)
            if not changed.any():
                continue
            inside = cv2.boxFilter(changed, -1, (self._s_in, self._s_in), borderType=cv2.BORDER_CONSTANT)
            big = cv2.boxFilter(changed, -1, (self._s_out, self._s_out), normalize=False,
                                borderType=cv2.BORDER_CONSTANT)
            mid = cv2.boxFilter(changed, -1, (self._s_mid, self._s_mid), normalize=False,
                                borderType=cv2.BORDER_CONSTANT)
            outside = (big - mid) / float(self._s_out ** 2 - self._s_mid ** 2)
            score = np.where((inside >= cfg.appear_min_inside) & (outside <= cfg.appear_max_outside), inside, 0)
            peaks = ((score > 0) & (score >= cv2.dilate(score, self._nms))).astype(np.uint8)
            # One spot per plateau of equal peaks, best first, a few per frame at most.
            n, labels = cv2.connectedComponents(peaks)
            spots = []
            for k in range(1, n):
                vs, us = np.nonzero(labels == k)
                spots.append((float(score[vs[0], us[0]]), float(us.mean()), float(vs.mean())))
            for _, u, v in sorted(spots, reverse=True)[:cfg.max_onsets_per_frame]:
                x, y = rx0 + (u + 0.5) * d, ry0 + (v + 0.5) * d
                if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                    continue
                onset = self._confirm_onset(imgs, j, x, y, box)
                if onset and all(math.hypot(o.x - onset.x, o.y - onset.y) > self.radius
                                 for o in onsets if o.frame == j):
                    onsets.append(onset)
        return onsets

    def _confirm_onset(self, imgs: list[np.ndarray], j: int, x: float, y: float, box: Box) -> _Onset | None:
        """Template-match around a coarse appearance at (x, y) in frame j and re-test at full resolution."""
        if not self.appears(imgs[j - 1], imgs[j], x, y, strict=False):  # cheap; rejects most spots
            return None
        r = self.cfg.change_downscale * 2
        region = (max(box[0], x - r), max(box[1], y - r), min(box[2], x + r), min(box[3], y + r))
        maps = self._maps([imgs[j - 1], imgs[j]], region)
        before, now = maps.scores
        ok = (now >= self.onset_floor) & (now - before >= self.cfg.onset_min_jump)
        if not ok.any():
            return None
        v, u = np.unravel_index(np.argmax(np.where(ok, now, -np.inf)), now.shape)
        cx, cy = maps.x0 + int(u), maps.y0 + int(v)
        if not self.appears(imgs[j - 1], imgs[j], cx, cy):
            return None
        return _Onset(j, float(cx), float(cy), float(now[v, u]))

    def _trace(self, ts: list[float], imgs: list[np.ndarray], onset: _Onset) -> _Trace:
        """Follow ``onset`` forwards until the score falls ``lift_drop`` below its best."""
        cfg = self.cfg
        near = max(2.0, self.radius * 0.5)
        x, y, best = onset.x, onset.y, onset.score
        points = [(ts[onset.frame], x, y)]
        lift = onset.frame
        for j in range(onset.frame + 1, len(imgs)):
            floor = max(best - cfg.lift_drop, self.onset_floor)
            hit = self._best_in([imgs[j]], x, y, near, floor)
            if hit is None:  # moved (swipe): only accept spots the circle just arrived at
                hit = self._best_in([imgs[j - 1], imgs[j]], x, y, self.walk_step, floor, cfg.local_min_rise)
            if hit is None:
                break
            x, y, score = hit
            best = max(best, score)
            points.append((ts[j], x, y))
            lift = j
        return _Trace(onset, lift, points, best)

    # --- public -------------------------------------------------------------------------

    def locate(self, reader: VideoReader, cand: Candidate) -> Located:
        """Find ``cand``'s circle in the video read by ``reader``."""
        cfg = self.cfg
        w0, w1 = cand.window
        # One frame before the window (for the first difference) and a margin after it (for the lift).
        frames = reader.frames_between(w0 - 100.0, w1 + cfg.walk_margin_ms, fmt="gray")
        cx, cy = (cand.box[0] + cand.box[2]) / 2, (cand.box[1] + cand.box[3]) / 2

        def not_found(reason: str) -> Located:
            return Located(cand, NOT_FOUND, cand.log_event_ms, cand.log_event_ms, cx, cy, reason=reason)

        if len(frames) < 2:
            return not_found("no frames decoded in the search window")
        ts = [t for t, _ in frames]
        imgs = [f for _, f in frames]
        in_window = np.array([w0 <= t <= w1 for t in ts])

        traces: list[_Trace] = []
        for onset in self._onsets(imgs, in_window, cand.box):
            # An "appearance" while an earlier touch is still down at the same spot is the
            # background changing under the circle, not a new touch.
            t_on = ts[onset.frame]
            if any(tr.onset.frame < onset.frame <= tr.lift and any(
                    t == t_on and math.hypot(px - onset.x, py - onset.y) <= 2 * self.radius
                    for t, px, py in tr.points) for tr in traces):
                continue
            traces.append(self._trace(ts, imgs, onset))

        good = [tr for tr in traces if tr.best >= cfg.local_match_threshold]
        if good:
            # Latest touch-down, preferring those before the log event (the finger goes down
            # first; the window extends past the event only to absorb clock-anchor error).
            # When the log bounds the lift, anything before that bound qualifies: the video
            # can lag the log clock by a few tens of ms.
            latest_down = cand.lift_by_ms if cand.lift_by_ms is not None else cand.log_event_ms
            tr = max(good, key=lambda tr: (ts[tr.onset.frame] <= latest_down, tr.onset.frame))
            o = tr.onset
            if cand.lift_by_ms is not None:  # the log says the finger was up by then
                kept = [p for p in tr.points if p[0] <= cand.lift_by_ms] or tr.points[:1]
                tr = _Trace(o, o.frame + len(kept) - 1, kept, tr.best)
            log.debug("%s candidate at %.0f ms -> touch %.0f ms at (%.0f, %.0f), score %.2f",
                      cand.source, cand.log_event_ms, ts[o.frame], o.x, o.y, tr.best)
            return Located(cand, FOUND, ts[o.frame], ts[tr.lift], o.x, o.y, tr.best, tr.points)
        reason = f"no circle appeared in the search box between {w0 / 1000:.2f}s and {w1 / 1000:.2f}s"
        if traces:
            reason += f"; weak candidates below {cfg.local_match_threshold:.2f}: " + ", ".join(
                f"({tr.onset.x:.0f}, {tr.onset.y:.0f}) at {ts[tr.onset.frame] / 1000:.2f}s scored {tr.best:.2f}"
                for tr in traces[:3])
        return not_found(reason)
