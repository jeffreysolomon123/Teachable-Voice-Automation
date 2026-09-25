"""Combine CV-model UI elements and OCR text boxes using pixel coordinates.

Each OCR box is assigned to the smallest CV element that contains most of it
(fraction of the text box inside the element >= --contain). CV elements get the
text they contain plus a parent/children hierarchy (smallest enclosing element).
OCR boxes not inside any CV element are kept as standalone "text" elements.

Usage:  python combine_results.py screenshot.png [-d out_dir]
        (expects <stem>.json from segment_ui.py and <stem>_ocr.json from ocr_text.py in out_dir)
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def draw_label(img, text, x, y, color):
    """Draw an id tag with a filled background so it stays readable on any image."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    y0 = max(y, th + 4)
    cv2.rectangle(img, (x, y0 - th - 4), (x + tw + 4, y0), color, -1)
    cv2.putText(img, text, (x + 2, y0 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def inside_frac(inner, outer):
    """Fraction of `inner` covered by `outer`."""
    x1, y1, x2, y2 = max(inner[0], outer[0]), max(inner[1], outer[1]), min(inner[2], outer[2]), min(inner[3], outer[3])
    return max(0, x2 - x1) * max(0, y2 - y1) / (area(inner) + 1e-9)


def combine(cv_elements, texts, contain=0.6):
    elements = [dict(e, source="cv", texts=[], children=[], parent=None) for e in cv_elements]

    # Hierarchy: parent = smallest other CV element that (almost) fully contains this one
    for e in elements:
        best = None
        for o in elements:
            if o is e or area(o["bbox"]) <= area(e["bbox"]):
                continue
            if inside_frac(e["bbox"], o["bbox"]) >= 0.9 and (best is None or area(o["bbox"]) < area(best["bbox"])):
                best = o
        if best:
            e["parent"] = best["id"]
            best["children"].append(e["id"])

    # Text assignment: smallest CV element containing the text box
    orphans = []
    for t in texts:
        best = None
        for e in elements:
            if inside_frac(t["bbox"], e["bbox"]) >= contain and (best is None or area(e["bbox"]) < area(best["bbox"])):
                best = e
        if best:
            best["texts"].append({"text": t["text"], "bbox": t["bbox"], "confidence": t["confidence"]})
        else:
            orphans.append(t)

    for e in elements:
        e["texts"].sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
        e["text"] = " ".join(t["text"] for t in e["texts"])
        e["cv_type"] = e.pop("type")
        e["type"] = "container" if e["children"] else ("labeled_element" if e["texts"] else "icon_or_image")

    next_id = len(elements)
    for t in orphans:
        elements.append({"id": next_id, "source": "ocr", "type": "text", "cv_type": None, "bbox": t["bbox"],
                         "center": t["center"], "size": t["size"], "confidence": t["confidence"],
                         "text": t["text"], "texts": [], "children": [], "parent": None})
        next_id += 1

    elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    return elements


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-d", "--dir", default="segment_out")
    ap.add_argument("--contain", type=float, default=0.6, help="min fraction of a text box inside an element")
    args = ap.parse_args()

    elements = combine_image(args.image, args.dir, args.contain)
    print(f"{'id':>3}  {'type':<16} {'src':<4} {'bbox':<22} text")
    for e in elements:
        print(f"{e['id']:>3}  {e['type']:<16} {e['source']:<4} {str(e['bbox']):<22} {e['text'][:60]}")
    print(f"{len(elements)} combined elements -> {Path(args.dir).resolve()}")


def combine_image(image, d, contain=0.6):
    """Merge <stem>.json and <stem>_ocr.json in ``d`` into <stem>_combined.json (+ annotated png)."""
    d, stem = Path(d), Path(image).stem
    cv_data = json.loads((d / f"{stem}.json").read_text(encoding="utf-8"))
    ocr_data = json.loads((d / f"{stem}_ocr.json").read_text(encoding="utf-8"))

    elements = combine(cv_data["elements"], ocr_data["texts"], contain)
    (d / f"{stem}_combined.json").write_text(
        json.dumps({"image": str(image), "width": cv_data["width"], "height": cv_data["height"],
                    "elements": elements}, indent=2, ensure_ascii=False), encoding="utf-8")

    img = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    colors = {"cv": (0, 0, 255), "ocr": (0, 170, 0)}
    for e in elements:
        x1, y1, x2, y2 = e["bbox"]
        cv2.rectangle(img, (x1, y1), (x2, y2), colors[e["source"]], 2)
        for t in e["texts"]:
            tx1, ty1, tx2, ty2 = t["bbox"]
            cv2.rectangle(img, (tx1, ty1), (tx2, ty2), (0, 170, 0), 1)
    for e in elements:  # labels last so they sit on top of all boxes
        draw_label(img, str(e["id"]), e["bbox"][0], e["bbox"][1], colors[e["source"]])
    _, buf = cv2.imencode(".png", img)
    buf.tofile(str(d / f"{stem}_combined_annotated.png"))
    return elements


if __name__ == "__main__":
    main()
