"""Run OCR (EasyOCR) on a screenshot and save text boxes in pixel coordinates.

Install:  pip install easyocr opencv-python numpy
Usage:    python ocr_text.py screenshot.png [-o out_dir] [--lang en] [--gpu] [--min-conf 0.3]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import easyocr
import numpy as np

# Windows consoles default to cp1252, which breaks EasyOCR's download bar and non-ASCII text
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")


def run_ocr(reader, img, min_conf):
    h, w = img.shape[:2]
    texts = []
    for pts, text, conf in reader.readtext(img, paragraph=False):
        text = text.strip()
        if conf < min_conf or not text:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x1, y1, x2, y2 = max(0, int(min(xs))), max(0, int(min(ys))), min(w, int(max(xs))), min(h, int(max(ys)))
        texts.append({"type": "text", "text": text, "bbox": [x1, y1, x2, y2],
                      "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)],
                      "size": [x2 - x1, y2 - y1], "confidence": round(float(conf), 3)})
    texts.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    for i, t in enumerate(texts):
        t["id"] = i
    return texts


def save_png(path, img):
    _, buf = cv2.imencode(".png", img)
    buf.tofile(str(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--out-dir", default="segment_out")
    ap.add_argument("--lang", nargs="+", default=["en"])
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--min-conf", type=float, default=0.3)
    args = ap.parse_args()

    texts = ocr_image(easyocr.Reader(args.lang, gpu=args.gpu), args.image, args.out_dir, args.min_conf)
    for t in texts:
        print(f"{t['id']:>3}  {t['bbox']}  {t['confidence']:.2f}  {t['text']}")
    print(f"{len(texts)} text boxes -> {Path(args.out_dir).resolve()}")


def ocr_image(reader, image, out_dir, min_conf=0.3):
    """OCR ``image`` and write <stem>_ocr.json and <stem>_ocr_annotated.png to ``out_dir``."""
    img = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Could not read image: {image}")
    h, w = img.shape[:2]

    texts = run_ocr(reader, img, min_conf)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image).stem
    (out_dir / f"{stem}_ocr.json").write_text(
        json.dumps({"image": str(image), "width": w, "height": h, "texts": texts}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    vis = img.copy()
    for t in texts:
        x1, y1, x2, y2 = t["bbox"]
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 170, 0), 2)
        label = str(t["id"])
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        y0 = max(y1, th + 4)
        cv2.rectangle(vis, (x1, y0 - th - 4), (x1 + tw + 4, y0), (0, 170, 0), -1)
        cv2.putText(vis, label, (x1 + 2, y0 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    save_png(out_dir / f"{stem}_ocr_annotated.png", vis)
    return texts


if __name__ == "__main__":
    main()
