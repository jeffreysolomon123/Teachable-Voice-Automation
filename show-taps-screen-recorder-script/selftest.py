"""Self-test: build a synthetic show-taps video and check the swipe/tap/long-press labels.

Usage: python selftest.py [--work selftest_out]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

import calibrate
import evaluate
from config import Config
from extractor import extract_taps

W, H, FPS, RADIUS = 540, 960, 30, 20

# (label, start_s, end_s, start_xy, end_xy)
GESTURES = [
    ("SWIPE", 0.5, 1.0, (100, 700), (440, 300)),
    ("TAP", 2.0, 2.15, (270, 500), (270, 500)),
    ("LONG_PRESS", 3.5, 4.5, (200, 800), (200, 800)),
]
DURATION_S = 6.0


def make_background() -> np.ndarray:
    """A textured background resembling app UI, so matching is not trivially easy."""
    rng = np.random.default_rng(0)
    bg = np.full((H, W, 3), 235, np.uint8)
    for _ in range(40):
        x, y = int(rng.integers(0, W - 120)), int(rng.integers(0, H - 40))
        color = tuple(int(c) for c in rng.integers(40, 220, 3))
        cv2.rectangle(bg, (x, y), (x + int(rng.integers(40, 200)), y + int(rng.integers(15, 60))), color, -1)
    for i in range(25):
        cv2.putText(bg, "Lorem ipsum 123", (10, 40 + i * 36), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 60, 60), 1)
    return bg


def draw_touch(frame: np.ndarray, x: float, y: float) -> None:
    """Draw an Android-like show-taps indicator: translucent grey disk with a light ring."""
    overlay = frame.copy()
    cv2.circle(overlay, (round(x), round(y)), RADIUS, (150, 150, 150), -1, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, dst=frame)
    cv2.circle(frame, (round(x), round(y)), RADIUS, (255, 255, 255), 2, cv2.LINE_AA)


def make_video(path: Path) -> None:
    """Write the synthetic recording to ``path``."""
    bg = make_background()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(int(DURATION_S * FPS)):
        t = i / FPS
        frame = bg.copy()
        for _, t0, t1, (x0, y0), (x1, y1) in GESTURES:
            if t0 <= t <= t1:
                a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                draw_touch(frame, x0 + a * (x1 - x0), y0 + a * (y1 - y0))
        writer.write(frame)
    writer.release()


def main(argv: list[str] | None = None) -> int:
    """Generate, calibrate, extract and verify. Returns 0 if all labels are correct."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path("selftest_out"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)

    video = work / "synthetic.mp4"
    make_video(video)
    calib_dir = work / "calibration"
    # Calibrate on the long press (stationary circle at 200, 800) at t=4.0 s.
    rc = calibrate.main([str(video), "--time", "4.0", "--x", "200", "--y", "800",
                         "--radius", str(RADIUS), "--out-dir", str(calib_dir)])
    if rc != 0:
        return rc

    result = extract_taps(video, work / "out", Config(calibration_dir=calib_dir), debug=True)

    truth = [{"type": g[0], "approx_time_s": g[1]} for g in GESTURES]
    (work / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print()
    evaluate.main([str(work / "out"), str(work / "ground_truth.json")])

    matches, extras = evaluate.match_events(truth, result.events_report)
    ok = not extras and all(m.detected and m.detected["label"] == m.expected_type for m in matches)
    ok = ok and len(result.taps) == 1
    for tap in result.taps:
        before = cv2.imread(str(work / "out" / tap.before))
        ok = ok and before is not None
    print("\nSELF-TEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
