"""Detect/segment UI elements in a mobile screenshot with a computer-vision model (YOLO).

Default weights are OmniParser's UI element detector (YOLOv8 trained on screenshots),
downloaded from Hugging Face. Any Ultralytics YOLO detection or segmentation weights
can be used via --weights (e.g. your own model trained on a UI dataset).
If the model is a segmentation model, per-element masks are also saved.

Install:  pip install ultralytics huggingface_hub opencv-python numpy
Usage:    python segment_ui.py screenshot.png [-o out_dir] [--conf 0.05] [--crops]
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

HF_REPO, HF_FILE = "microsoft/OmniParser-v2.0", "icon_detect/model.pt"


def load_model(weights):
    if weights is None:
        from huggingface_hub import hf_hub_download
        weights = hf_hub_download(HF_REPO, HF_FILE)
    return YOLO(weights)


def detect(model, img, conf, iou_thresh, imgsz, device):
    res = model.predict(img, conf=conf, iou=iou_thresh, imgsz=imgsz, device=device, verbose=False)[0]
    h, w = img.shape[:2]
    names = res.names
    elements, masks = [], []
    for i, box in enumerate(res.boxes):
        x1, y1, x2, y2 = (int(round(v)) for v in box.xyxy[0].tolist())
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        cls = int(box.cls[0])
        elements.append({"type": names.get(cls, str(cls)), "bbox": [x1, y1, x2, y2],
                         "confidence": round(float(box.conf[0]), 3)})
        if res.masks is not None:
            m = cv2.resize(res.masks.data[i].cpu().numpy(), (w, h), interpolation=cv2.INTER_NEAREST)
            masks.append(m > 0.5)
        else:
            masks.append(None)
    return elements, masks


def add_location(e, w, h):
    """Add pixel/normalized geometry and a coarse screen region to an element."""
    x1, y1, x2, y2 = e["bbox"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    e["center"] = [round(cx), round(cy)]
    e["size"] = [x2 - x1, y2 - y1]
    e["bbox_norm"] = [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)]
    e["center_norm"] = [round(cx / w, 4), round(cy / h, 4)]
    e["area_pct"] = round(100 * (x2 - x1) * (y2 - y1) / (w * h), 2)
    col = "left" if cx < w / 3 else "center" if cx < 2 * w / 3 else "right"
    row = "top" if cy < h / 3 else "middle" if cy < 2 * h / 3 else "bottom"
    e["region"] = f"{row}-{col}"


def print_table(elements):
    print(f"{'id':>3}  {'type':<10} {'x1':>5} {'y1':>5} {'x2':>5} {'y2':>5}  {'center':>11}  {'size':>9}  {'conf':>5}  region")
    for e in elements:
        x1, y1, x2, y2 = e["bbox"]
        print(f"{e['id']:>3}  {e['type']:<10} {x1:>5} {y1:>5} {x2:>5} {y2:>5}  "
              f"{str(tuple(e['center'])):>11}  {e['size'][0]:>4}x{e['size'][1]:<4}  {e['confidence']:>5}  {e['region']}")


def annotate(img, elements, masks):
    out = img.copy()
    rng = np.random.default_rng(0)
    for e, m in zip(elements, masks):
        color = tuple(int(c) for c in rng.integers(60, 255, 3))
        if m is not None:
            out[m] = (0.6 * out[m] + 0.4 * np.array(color)).astype(np.uint8)
        x1, y1, x2, y2 = e["bbox"]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        cv2.putText(out, f"{e['id']}", (x1 + 2, max(12, y1 - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def save_png(path, img):
    _, buf = cv2.imencode(".png", img)
    buf.tofile(str(path))  # works with non-ASCII Windows paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--out-dir", default="segment_out")
    ap.add_argument("--weights", help="path to YOLO .pt weights (default: OmniParser icon_detect)")
    ap.add_argument("--conf", type=float, default=0.05, help="confidence threshold (UI models need a low one)")
    ap.add_argument("--iou", type=float, default=0.1, help="NMS IoU threshold")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=None, help="e.g. cpu, 0")
    ap.add_argument("--crops", action="store_true", help="save a cropped image per element")
    args = ap.parse_args()

    img = cv2.imdecode(np.fromfile(args.image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Could not read image: {args.image}")

    model = load_model(args.weights)
    elements, masks = detect(model, img, args.conf, args.iou, args.imgsz, args.device)

    # Sort top-to-bottom, left-to-right and assign ids (keep masks aligned)
    order = sorted(range(len(elements)), key=lambda i: (elements[i]["bbox"][1], elements[i]["bbox"][0]))
    elements, masks = [elements[i] for i in order], [masks[i] for i in order]
    h, w = img.shape[:2]
    for i, e in enumerate(elements):
        e["id"] = i
        add_location(e, w, h)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.image).stem

    (out_dir / f"{stem}.json").write_text(
        json.dumps({"image": args.image, "width": w, "height": h, "elements": elements}, indent=2),
        encoding="utf-8")
    save_png(out_dir / f"{stem}_annotated.png", annotate(img, elements, masks))

    if args.crops:
        crop_dir = out_dir / f"{stem}_crops"
        crop_dir.mkdir(exist_ok=True)
        for e, m in zip(elements, masks):
            x1, y1, x2, y2 = e["bbox"]
            crop = img[y1:y2, x1:x2]
            if m is not None:  # transparent background outside the mask
                alpha = (m[y1:y2, x1:x2] * 255).astype(np.uint8)
                crop = np.dstack([crop, alpha])
            save_png(crop_dir / f"{e['id']:03d}_{e['type']}.png", crop)

    print_table(elements)
    print(f"{len(elements)} elements ({w}x{h}) -> {out_dir.resolve()}")


if __name__ == "__main__":
    main()
