"""CLI: turn taps.json + events_report.json into flow.json (ordered semantic steps).

Usage: python flow_builder_cli.py taps.json events_report.json --out flow.json
                                  [--frames-dir DIR | --no-copy-frames]

Consecutive keystrokes become one type_text step (keystrokes the video missed are spliced
back in from events_report.json); every other tap is a tap step. The before/touch/after
frames of each step are copied to DIR (default: <out stem>_frames next to flow.json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flow_builder import FlowBuildError, build_flow


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, build flow.json and print a summary. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("taps", type=Path, help="taps.json written by extract_taps.py")
    parser.add_argument("events_report", type=Path, help="events_report.json from the same run")
    parser.add_argument("--out", type=Path, required=True, help="flow.json to write")
    frames = parser.add_mutually_exclusive_group()
    frames.add_argument("--frames-dir", type=Path,
                        help="folder for the steps' frames (default: <out stem>_frames next to --out)")
    frames.add_argument("--no-copy-frames", action="store_true",
                        help="reference the original frames instead of copying them")
    args = parser.parse_args(argv)

    try:
        result = build_flow(args.taps, args.events_report, args.out,
                            frames_dir=args.frames_dir, copy_frames=not args.no_copy_frames)
    except FlowBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for w in result.warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    s = result.summary
    print(f"\nFlow written -> {result.out_path}")
    if result.frames_dir:
        print(f"Frames ({result.copied_frames}) -> {result.frames_dir}")
    print(f"  total steps:            {s['total_steps']}")
    print(f"  type_text steps:        {s['type_text']}"
          + (f"  ({s['reconstructed_chars']} char(s) reconstructed from log)" if s["reconstructed_chars"] else ""))
    print(f"  tap steps:              {s['tap']}")
    print(f"  needs visual grounding: {s['needs_visual_grounding']}  (element_trustworthy == false)")
    print()
    for step in result.steps:
        if step["type"] == "type_text":
            print(f"  [{step['step_index']:>2}] type_text {step['text']!r:<20} taps {step['source_tap_ids']}")
        else:
            el = step["element"] or {}
            flag = "" if step["element_trustworthy"] else "  (visual grounding)"
            print(f"  [{step['step_index']:>2}] tap ({step['tap_x']}, {step['tap_y']}) "
                  f"{step['source']:<13} {el.get('text')!r}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
