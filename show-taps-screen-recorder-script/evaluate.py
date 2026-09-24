"""CLI: compare extract_taps.py results against a ground-truth gesture list.

Usage: python evaluate.py output_dir ground_truth.json

ground_truth.json: [{"type": "TAP", "approx_time_s": 2.1}, {"type": "SWIPE", "approx_time_s": 4.0}, ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

TOLERANCE_S = 0.5


@dataclass
class Match:
    """A ground-truth gesture and the detected event paired with it (if any)."""

    expected_type: str
    expected_s: float
    detected: dict | None


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
        Match(g["type"].upper(), float(g["approx_time_s"]), events[g_to_e[gi]] if gi in g_to_e else None)
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
    events = json.loads(report_path.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))

    matches, extras = match_events(truth, events)

    print(f"{'#':>3}  {'expected':<12} {'time_s':>7}  {'detected':<12} {'start_s':>7} {'end_s':>7}  result")
    print("-" * 70)
    for i, m in enumerate(matches, 1):
        if m.detected is None:
            print(f"{i:>3}  {m.expected_type:<12} {m.expected_s:>7.2f}  {'-':<12} {'':>7} {'':>7}  MISSED")
            continue
        d = m.detected
        result = "OK" if d["label"] == m.expected_type else "WRONG LABEL"
        print(f"{i:>3}  {m.expected_type:<12} {m.expected_s:>7.2f}  {d['label']:<12} "
              f"{d['start_ms'] / 1000:>7.2f} {d['end_ms'] / 1000:>7.2f}  {result}")
    if extras:
        print("\nDetected events with no ground-truth match:")
        for e in extras:
            print(f"     {'-':<12} {'':>7}  {e['label']:<12} {e['start_ms'] / 1000:>7.2f} "
                  f"{e['end_ms'] / 1000:>7.2f}  EXTRA ({e['reason']})")

    precision, recall, tp, detected, expected = tap_metrics(matches, extras)
    print(f"\nTAP precision: {precision:.2%} ({tp}/{detected})")
    print(f"TAP recall:    {recall:.2%} ({tp}/{expected})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
