"""CLI: attach a vision-grounded element to every tap step of flow.json.

Usage: python ground_flow_cli.py flow.json --out grounded_flow.json --segmentation-dir DIR
                                 [--force-rerun] [--only-untrustworthy] [--python PYTHON]

Each tap step's before_frame is run through DIR's segment_ui.py / ocr_text.py /
combine_results.py (cached in segment_out/ next to the frame), and the element under the
tap point is added as "grounded_element". Original "element" fields are kept.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ground_flow import GroundingError, ground_flow


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, ground the flow and print a summary. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flow", type=Path, help="flow.json from flow_builder_cli.py")
    parser.add_argument("--out", type=Path, required=True, help="grounded_flow.json to write")
    parser.add_argument("--segmentation-dir", type=Path, required=True,
                        help="folder with segment_ui.py, ocr_text.py, combine_results.py")
    parser.add_argument("--force-rerun", action="store_true",
                        help="re-run segmentation even if a _combined.json already exists")
    parser.add_argument("--only-untrustworthy", action="store_true",
                        help="only ground tap steps with element_trustworthy == false")
    parser.add_argument("--python", help="interpreter for the segmentation scripts "
                                         "(needs ultralytics + easyocr; default: this one)")
    args = parser.parse_args(argv)

    try:
        result = ground_flow(args.flow, args.out, args.segmentation_dir, python=args.python,
                             force_rerun=args.force_rerun, only_untrustworthy=args.only_untrustworthy)
    except GroundingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\nGrounded flow written -> {args.out}")
    print(f"  frames segmented / reused: {result.segmented_frames} / {result.reused_frames}")
    print(f"  grounded 'contained':        {result.contained}")
    print(f"  grounded 'fallback_nearest': {result.fallback_nearest}")
    print(f"  grounded 'log_bounds':       {result.log_bounds}  (detector missed the clicked element; logged bounds used)")
    if result.skipped:
        print(f"  skipped (trustworthy):       {result.skipped}")
    print()
    for s in result.steps:
        g = s.get("grounded_element")
        if g:
            log_text = (s.get("element") or {}).get("text")
            print(f"  [{s['step_index']:>2}] ({s['tap_x']}, {s['tap_y']}) -> #{g['id']} {g['type']:<15} "
                  f"{g['text']!r:<30} log: {log_text!r}  [{g['grounded_confidence']}]")
    if result.review_flags:
        print(f"\nFlag for manual review ({len(result.review_flags)}): grounded text disagrees with log text")
        for f in result.review_flags:
            print(f"  step {f.step_index}: log {f.element_text!r} vs grounded {f.grounded_text!r}")
    else:
        print("\nNo text disagreements between log and grounded elements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
