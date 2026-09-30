"""Benchmark speed/accuracy variants of segment_api.segment() against a reference result.

    python bench.py <image> <reference.json> [reps]
"""
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from segment_api import segment

VARIANTS = {  # name: (max_side, compact, fast_provider)
    "baseline (full-res, keyed JSON, any provider)": (0, False, False),
    "fast provider": (0, False, True),
    "fast provider + 1280px": (1280, False, True),
    "fast provider + compact rows": (0, True, True),
    "compact + 1280px + fast provider": (1280, True, True),
    "compact + 1024px + fast provider": (1024, True, True),
    "compact + 768px + fast provider": (768, True, True),
}


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def score(elements, ref):
    """Recall of reference elements (IoU >= 0.5), mean IoU of matches, exact-text rate of text matches."""
    hits, ious, text_ok, text_n = 0, [], 0, 0
    for r in ref:
        best = max(elements, key=lambda e: iou(e["bbox"], r["bbox"]), default=None)
        if best and iou(best["bbox"], r["bbox"]) >= 0.5:
            hits += 1
            ious.append(iou(best["bbox"], r["bbox"]))
            if r["text"]:
                text_n += 1
                text_ok += " ".join(best["text"].split()) == " ".join(r["text"].split())
    return hits / len(ref), statistics.mean(ious) if ious else 0, text_ok / max(text_n, 1)


def run(name, img, ref, reps):
    max_side, compact, fast = VARIANTS[name]
    rows = []
    for _ in range(reps):
        els, info = segment(img, max_side=max_side, compact=compact, fast_provider=fast)
        u = info["usage"]
        rows.append((info["latency_s"], info["provider"], u.get("prompt_tokens"), u.get("completion_tokens"),
                     u.get("cost", 0), len(els), *score(els, ref), info["upload_kb"], info["attempts"]))
    return name, rows


def main():
    img_path, ref_path = sys.argv[1], sys.argv[2]
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    ref = json.loads(Path(ref_path).read_text(encoding="utf-8"))["elements"]

    with ThreadPoolExecutor(len(VARIANTS)) as ex:
        results = list(ex.map(lambda n: run(n, img, ref, reps), VARIANTS))

    print(f"\n{'variant':<48} {'latency s (runs)':<22} {'med':>5} {'in':>5} {'out':>5} {'els':>4} "
          f"{'recall':>6} {'mIoU':>5} {'text':>5} {'KB':>5} {'$/call':>8}  providers")
    for name, rows in results:
        lat = [r[0] for r in rows]
        med = lambda i: statistics.median(r[i] or 0 for r in rows)
        print(f"{name:<48} {' '.join(f'{x:.1f}' for x in lat):<22} {statistics.median(lat):>5.1f} "
              f"{med(2):>5.0f} {med(3):>5.0f} {med(5):>4.0f} {med(6):>6.2f} {med(7):>5.2f} {med(8):>5.2f} "
              f"{med(9):>5.0f} {med(4):>8.5f}  {sorted(set(r[1] for r in rows))}"
              + (f" retries={sum(r[10] - 1 for r in rows)}" if any(r[10] > 1 for r in rows) else ""))


if __name__ == "__main__":
    main()
