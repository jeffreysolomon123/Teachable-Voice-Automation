"""CLI: learn the show-taps circle's appearance from a frame of your recording.

Usage:
  python calibrate.py video.mp4 --time 3.2                          (click in a window)
  python calibrate.py video.mp4 --time 3.2 --x 540 --y 1200 --radius 40   (no GUI)

In the window: click the circle's center, then click on (or drag to) its edge.
Enter/Space = save, R = reset, Esc = cancel.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import cv2
import numpy as np

from config import Config
from video_io import VideoError, get_frame_at

log = logging.getLogger(__name__)

MAX_WINDOW_HEIGHT = 900


def pick_circle_gui(frame: np.ndarray) -> tuple[int, int, int] | None:
    """Let the user click the center and radius in an OpenCV window.

    Returns ``(x, y, radius)`` in original frame pixels, or None if cancelled.
    """
    scale = min(1.0, MAX_WINDOW_HEIGHT / frame.shape[0])
    base = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame.copy()
    state: dict = {"center": None, "radius": 0.0, "dragging": False, "done": False}

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            if state["center"] is None or state["done"]:
                state.update(center=(x, y), radius=0.0, dragging=True, done=False)
            else:  # second click sets the radius
                cx, cy = state["center"]
                state.update(radius=math.hypot(x - cx, y - cy), done=True)
        elif event == cv2.EVENT_MOUSEMOVE and state["center"] is not None and not state["done"]:
            cx, cy = state["center"]
            state["radius"] = math.hypot(x - cx, y - cy)
        elif event == cv2.EVENT_LBUTTONUP and state["dragging"]:
            state["dragging"] = False
            if state["radius"] > 3:  # a drag defined the radius
                state["done"] = True

    window = "calibrate: click center, then edge | Enter=save R=reset Esc=cancel"
    try:
        cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    except cv2.error as exc:
        raise RuntimeError("No GUI available; pass --x --y --radius instead") from exc
    cv2.setMouseCallback(window, on_mouse)
    try:
        while True:
            view = base.copy()
            if state["center"] is not None:
                c = state["center"]
                cv2.drawMarker(view, c, (0, 0, 255), cv2.MARKER_CROSS, 12, 1)
                if state["radius"] > 0:
                    color = (0, 255, 0) if state["done"] else (0, 255, 255)
                    cv2.circle(view, c, round(state["radius"]), color, 1)
            cv2.imshow(window, view)
            key = cv2.waitKey(20) & 0xFF
            if key in (13, 10, 32) and state["done"]:
                cx, cy = state["center"]
                return round(cx / scale), round(cy / scale), max(1, round(state["radius"] / scale))
            if key in (ord("r"), ord("R")):
                state.update(center=None, radius=0.0, dragging=False, done=False)
            if key == 27 or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                return None
    finally:
        cv2.destroyAllWindows()


def save_calibration(
    frame: np.ndarray, x: int, y: int, radius: int, out_dir: Path, cfg: Config, meta: dict
) -> dict[str, Path]:
    """Write frame.png, template.png and circle.json to ``out_dir``; return their paths."""
    h, w = frame.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError(f"Center ({x}, {y}) is outside the {w}x{h} frame")
    if radius < 2:
        raise ValueError("Radius must be at least 2 px")
    out_dir.mkdir(parents=True, exist_ok=True)
    half = radius + cfg.template_margin_px
    x0, y0 = max(0, x - half), max(0, y - half)
    x1, y1 = min(w, x + half + 1), min(h, y + half + 1)
    template = frame[y0:y1, x0:x1]

    paths = {
        "frame": out_dir / "frame.png",
        "template": out_dir / "template.png",
        "circle": out_dir / "circle.json",
    }
    cv2.imwrite(str(paths["frame"]), frame)
    cv2.imwrite(str(paths["template"]), template)
    data = {
        "radius_px": radius,
        "frame_width": w,
        "frame_height": h,
        "center_x": x,
        "center_y": y,
        "template_center": [x - x0, y - y0],
        **meta,
    }
    paths["circle"].write_text(json.dumps(data, indent=2), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, pick the circle and write calibration files. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="screen recording (.mp4)")
    parser.add_argument("--time", type=float, required=True, help="seconds into the video where a finger is down")
    parser.add_argument("--x", type=int, help="circle center x (original pixels)")
    parser.add_argument("--y", type=int, help="circle center y (original pixels)")
    parser.add_argument("--radius", type=int, help="circle radius in pixels")
    parser.add_argument("--out-dir", type=Path, default=Path("calibration"), help="default: calibration")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    manual = [args.x, args.y, args.radius]
    if any(v is not None for v in manual) and not all(v is not None for v in manual):
        print("ERROR: --x, --y and --radius must be given together", file=sys.stderr)
        return 2

    try:
        ts_ms, frame = get_frame_at(args.video, args.time * 1000)
    except VideoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    log.info("Using frame at %.3f s (%dx%d)", ts_ms / 1000, frame.shape[1], frame.shape[0])

    if all(v is not None for v in manual):
        x, y, radius = args.x, args.y, args.radius
    else:
        try:
            picked = pick_circle_gui(frame)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if picked is None:
            print("Calibration cancelled; nothing saved.")
            return 1
        x, y, radius = picked

    try:
        paths = save_calibration(frame, x, y, radius, args.out_dir, Config(),
                                 {"video": str(args.video), "time_s": round(ts_ms / 1000, 3)})
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\nCalibration saved: circle at ({x}, {y}), radius {radius}px, "
          f"frame {frame.shape[1]}x{frame.shape[0]} (t={ts_ms / 1000:.3f}s)")
    print(f"  frame    -> {paths['frame']}")
    print(f"  template -> {paths['template']}")
    print(f"  circle   -> {paths['circle']}")
    print("Open template.png to check the crop is centered on the circle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
