"""CLI: compare extract_taps.py results against a ground-truth gesture list.

Usage: python evaluate.py output_dir ground_truth.json

ground_truth.json: [{"type": "TAP", "approx_time_s": 2.1}, {"type": "SWIPE", "approx_time_s": 4.0}, ...]
Optional per entry: "x", "y" (tap point in video pixels, to report the position error),
"source" (click / keyboard / window_change / unlogged: which log evidence should find it,
for per-source recall in --log mode) and "label" (a description, printed only).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

TOLERANCE_S = 0.5
NOT_FOUND = "NOT_FOUND"


@dataclass
class Match:
    """A ground-truth gesture and the detected event paired with it (if any)."""

    expected_type: str
    expected_s: float
    detected: dict | None
    truth: dict | None = None

    @property
    def correct(self) -> bool:
        """True if the gesture was detected with the expected label."""
        return self.detected is not None and self.detected["label"] == self.expected_type

    @property
    def position_error_px(self) -> float | None:
        """Distance between the true and detected tap point, if both are known."""
        t, d = self.truth or {}, self.detected or {}
        if "x" not in t or "y" not in t or "tap_x" not in d or "tap_y" not in d:
            return None
        return math.hypot(float(t["x"]) - d["tap_x"], float(t["y"]) - d["tap_y"])


def time_distance_s(event: dict, t_s: float) -> float:
    """Distance in seconds from ``t_s`` to the event's [start, end] interval (0 if inside)."""
    start, end = event["start_ms"] / 1000, event["end_ms"] / 1000
    return max(start - t_s, 0.0, t_s - end)


def match_events(truth: list[dict], events: list[dict], tolerance_s: float = TOLERANCE_S) -> tuple[list[Match], list[dict]]:
    """Pair each ground-truth gesture with the nearest unused event within ``tolerance_s``.

    Pairs are assigned greedily, closest first. Returns the matches (in ground-truth
    order) and the detected events left unmatched.
    """
    pairs = sorted(
        (time_distance_s(e, float(g["approx_time_s"])), gi, ei)
        for gi, g in enumerate(truth)
        for ei, e in enumerate(events)
        if time_distance_s(e, float(g["approx_time_s"])) <= tolerance_s
    )
    g_to_e: dict[int, int] = {}
    used: set[int] = set()
    for _, gi, ei in pairs:
        if gi not in g_to_e and ei not in used:
            g_to_e[gi] = ei
            used.add(ei)
    matches = [
        Match(g["type"].upper(), float(g["approx_time_s"]), events[g_to_e[gi]] if gi in g_to_e else None, g)
        for gi, g in enumerate(truth)
    ]
    extras = [e for ei, e in enumerate(events) if ei not in used]
    return matches, extras


def tap_metrics(matches: list[Match], extras: list[dict]) -> tuple[float, float, int, int, int]:
    """Return (precision, recall, true_pos, detected_taps, expected_taps) for the TAP label."""
    tp = sum(1 for m in matches if m.expected_type == "TAP" and m.detected and m.detected["label"] == "TAP")
    detected = sum(1 for m in matches if m.detected and m.detected["label"] == "TAP")
    detected += sum(1 for e in extras if e["label"] == "TAP")
    expected = sum(1 for m in matches if m.expected_type == "TAP")
    precision = tp / detected if detected else 0.0
    recall = tp / expected if expected else 0.0
    return precision, recall, tp, detected, expected


def source_metrics(matches: list[Match], extras: list[dict]) -> dict[str, dict[str, int]]:
    """Per log source: detected TAPs, how many were right, expected TAPs and how many were found.

    Detected TAPs are grouped by the event's ``source``; expected TAPs by the
    ground truth's ``source``. Sources only exist in --log mode output.
    """
    stats: dict[str, dict[str, int]] = {}

    def row(source: str) -> dict[str, int]:
        return stats.setdefault(source, {"detected": 0, "correct": 0, "expected": 0, "found": 0})

    for m in matches:
        if m.detected and m.detected["label"] == "TAP" and "source" in m.detected:
            r = row(m.detected["source"])
            r["detected"] += 1
            r["correct"] += m.expected_type == "TAP"
        if m.expected_type == "TAP" and m.truth and "source" in m.truth:
            r = row(m.truth["source"])
            r["expected"] += 1
            r["found"] += m.correct
    for e in extras:
        if e["label"] == "TAP" and "source" in e:
            row(e["source"])["detected"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    """Print the expected-vs-detected table and TAP precision/recall. Returns an exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path, help="directory written by extract_taps.py")
    parser.add_argument("ground_truth", type=Path, help="ground-truth JSON file")
    args = parser.parse_args(argv)

    report_path = args.output_dir / "events_report.json"
    for p in (report_path, args.ground_truth):
        if not p.is_file():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 1
    all_events = json.loads(report_path.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    # Log candidates whose circle was not found are not detections; list them separately.
    events = [e for e in all_events if e["label"] != NOT_FOUND]
    not_found = [e for e in all_events if e["label"] == NOT_FOUND]

    matches, extras = match_events(truth, events)

    print(f"{'#':>3}  {'expected':<12} {'time_s':>7}  {'detected':<12} {'start_s':>7} {'end_s':>7} "
          f"{'err_px':>6}  result")
    print("-" * 80)
    for i, m in enumerate(matches, 1):
        note = f"  ({m.truth['label']})" if m.truth and m.truth.get("label") else ""
        if m.detected is None:
            print(f"{i:>3}  {m.expected_type:<12} {m.expected_s:>7.2f}  {'-':<12} {'':>7} {'':>7} {'':>6}  MISSED{note}")
            continue
        d = m.detected
        err = m.position_error_px
        result = "OK" if m.correct else "WRONG LABEL"
        print(f"{i:>3}  {m.expected_type:<12} {m.expected_s:>7.2f}  {d['label']:<12} "
              f"{d['start_ms'] / 1000:>7.2f} {d['end_ms'] / 1000:>7.2f} "
              f"{'' if err is None else f'{err:.1f}':>6}  {result}{note}")
    if extras:
        print("\nDetected events with no ground-truth match:")
        for e in extras:
            print(f"     {'-':<12} {'':>7}  {e['label']:<12} {e['start_ms'] / 1000:>7.2f} "
                  f"{e['end_ms'] / 1000:>7.2f}  EXTRA ({e['reason']})")
    if not_found:
        print(f"\nLog candidates not found in the video: {len(not_found)}")
        for e in not_found:
            print(f"     {e.get('source', '?'):<13} log {e.get('log_event_ms', e['start_ms']) / 1000:>6.2f}s  "
                  f"{e['reason']}")

    precision, recall, tp, detected, expected = tap_metrics(matches, extras)
    print(f"\nTAP precision: {precision:.2%} ({tp}/{detected})")
    print(f"TAP recall:    {recall:.2%} ({tp}/{expected})")

    errors = [m.position_error_px for m in matches if m.correct and m.expected_type == "TAP"]
    errors = [e for e in errors if e is not None]
    if errors:
        print(f"Position error (correct TAPs with x/y): mean {sum(errors) / len(errors):.1f} px, "
              f"max {max(errors):.1f} px, <= 10 px: {sum(e <= 10 for e in errors)}/{len(errors)}")

    stats = source_metrics(matches, extras)
    if stats:
        print(f"\n{'source':<14} {'precision':>16} {'recall':>16}")
        for source, r in sorted(stats.items()):
            p = f"{r['correct'] / r['detected']:.0%} ({r['correct']}/{r['detected']})" if r["detected"] else "-"
            rc = f"{r['found'] / r['expected']:.0%} ({r['found']}/{r['expected']})" if r["expected"] else "-"
            print(f"{source:<14} {p:>16} {rc:>16}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
