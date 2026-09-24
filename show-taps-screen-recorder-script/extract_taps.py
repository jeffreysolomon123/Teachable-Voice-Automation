"""CLI: extract tap frames from an Android "Show taps" screen recording.

Usage: python extract_taps.py video.mp4 --out output_dir [--debug] [--calibration calibration]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import Config
from detector import CalibrationError
from extractor import NoCirclesError, extract_taps
from video_io import VideoError


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the extractor and print a summary. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="screen recording (.mp4)")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--debug", action="store_true", help="also write debug/annotated.mp4")
    parser.add_argument("--calibration", type=Path, default=Path("calibration"),
                        help="calibration directory written by calibrate.py (default: calibration)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    cfg = Config(calibration_dir=args.calibration)
    try:
        result = extract_taps(args.video, args.out, cfg, debug=args.debug)
    except (VideoError, CalibrationError, NoCirclesError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\n{len(result.taps)} tap(s) extracted from {result.frame_count} frames -> {result.out_dir}")
    print(f"Events by label: {result.labels}")
    print(f"  {result.out_dir / 'taps.json'}")
    print(f"  {result.out_dir / 'events_report.json'}")
    if result.debug_video:
        print(f"  {result.debug_video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
