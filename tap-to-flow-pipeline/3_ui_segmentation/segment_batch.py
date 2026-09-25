"""Run segment_ui -> ocr_text -> combine_results on many screenshots with the models loaded once.

Writes exactly what the three scripts write per image (<stem>.json, <stem>_ocr.json,
<stem>_combined.json and their annotated PNGs, plus crops with --crops). Loading YOLO and
EasyOCR dominates a single-image run, so batching saves most of the time.

Usage:  python segment_batch.py a.png b.png ... [-o out_dir] [--crops] [--conf 0.05] [--gpu]
"""
import argparse
import sys
import time
from pathlib import Path

import easyocr

from combine_results import combine_image
from ocr_text import ocr_image
from segment_ui import load_model, segment_image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-o", "--out-dir", default="segment_out")
    ap.add_argument("--weights", help="path to YOLO .pt weights (default: OmniParser icon_detect)")
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--iou", type=float, default=0.1)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=None)
    ap.add_argument("--crops", action="store_true")
    ap.add_argument("--lang", nargs="+", default=["en"])
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--min-conf", type=float, default=0.3)
    ap.add_argument("--contain", type=float, default=0.6)
    args = ap.parse_args()

    t0 = time.perf_counter()
    model = load_model(args.weights)
    reader = easyocr.Reader(args.lang, gpu=args.gpu)
    print(f"models loaded in {time.perf_counter() - t0:.1f}s", flush=True)

    for i, image in enumerate(args.images, start=1):
        t = time.perf_counter()
        segment_image(model, image, args.out_dir, args.conf, args.iou, args.imgsz, args.device, args.crops)
        ocr_image(reader, image, args.out_dir, args.min_conf)
        elements = combine_image(image, args.out_dir, args.contain)
        print(f"[{i}/{len(args.images)}] {Path(image).name}: {len(elements)} elements "
              f"in {time.perf_counter() - t:.1f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
