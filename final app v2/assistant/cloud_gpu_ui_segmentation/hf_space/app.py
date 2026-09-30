"""Hugging Face ZeroGPU Space for UI Segmentation (OmniParser v2 + EasyOCR).

Runs on a free dynamic NVIDIA H100 GPU via @spaces.GPU.
Provides both an interactive web UI and a programmatic REST API (via gradio_client or HTTP).

Hosted 24/7 on Hugging Face Spaces with zero credit/debit card required.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Tuple

import gradio as gr
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Import spaces for ZeroGPU dynamic H100 allocation
try:
    import spaces

    HAS_SPACES = True
except ImportError:
    HAS_SPACES = False

    class spaces:
        @staticmethod
        def GPU(duration=60):
            def decorator(fn):
                return fn

            return decorator

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hf_ui_segmentation")

HF_REPO = "microsoft/OmniParser-v2.0"
HF_FILE = "icon_detect/model.pt"

# Global model cache
YOLO_MODEL = None
OCR_READER = None


def get_models():
    """Lazy load models on the GPU when needed."""
    global YOLO_MODEL, OCR_READER
    import torch

    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    if YOLO_MODEL is None:
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO

        log.info(f"Loading OmniParser v2 from {HF_REPO} on {device}...")
        weights = hf_hub_download(HF_REPO, HF_FILE)
        YOLO_MODEL = YOLO(weights)

    if OCR_READER is None:
        import easyocr

        log.info(f"Loading EasyOCR on {device}...")
        use_gpu = torch.cuda.is_available()
        OCR_READER = easyocr.Reader(["en"], gpu=use_gpu)

    return YOLO_MODEL, OCR_READER


def compute_area(b: List[int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def inside_fraction(inner: List[int], outer: List[int]) -> float:
    x1, y1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x2, y2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    return overlap / (compute_area(inner) + 1e-9)


def add_location_metadata(e: Dict[str, Any], w: int, h: int) -> None:
    x1, y1, x2, y2 = e["bbox"]
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    e["center"] = [round(cx), round(cy)]
    e["size"] = [x2 - x1, y2 - y1]
    e["bbox_norm"] = [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)]
    e["center_norm"] = [round(cx / w, 4), round(cy / h, 4)]
    e["area_pct"] = round(100.0 * (x2 - x1) * (y2 - y1) / (w * h), 2)
    col = "left" if cx < w / 3 else "center" if cx < 2 * w / 3 else "right"
    row = "top" if cy < h / 3 else "middle" if cy < 2 * h / 3 else "bottom"
    e["region"] = f"{row}-{col}"


def merge_hierarchy_and_text(
    cv_elements: List[Dict[str, Any]],
    texts: List[Dict[str, Any]],
    contain: float = 0.6,
) -> List[Dict[str, Any]]:
    elements = [dict(e, source="cv", texts=[], children=[], parent=None) for e in cv_elements]

    # Parent containment
    for e in elements:
        best_parent = None
        for o in elements:
            if o is e or compute_area(o["bbox"]) <= compute_area(e["bbox"]):
                continue
            if inside_fraction(e["bbox"], o["bbox"]) >= 0.9:
                if best_parent is None or compute_area(o["bbox"]) < compute_area(best_parent["bbox"]):
                    best_parent = o
        if best_parent:
            e["parent"] = best_parent["id"]
            best_parent["children"].append(e["id"])

    # Text containment
    orphans = []
    for t in texts:
        best_elem = None
        for e in elements:
            if inside_fraction(t["bbox"], e["bbox"]) >= contain:
                if best_elem is None or compute_area(e["bbox"]) < compute_area(best_elem["bbox"]):
                    best_elem = e
        if best_elem:
            best_elem["texts"].append(
                {"text": t["text"], "bbox": t["bbox"], "confidence": t["confidence"]}
            )
        else:
            orphans.append(t)

    # Classify element types
    for e in elements:
        e["texts"].sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
        e["text"] = " ".join(t["text"] for t in e["texts"])
        e["cv_type"] = e.pop("type", "icon")
        e["type"] = (
            "container" if e["children"] else ("labeled_element" if e["texts"] else "icon_or_image")
        )

    # Standalone text elements
    next_id = len(elements)
    for t in orphans:
        elements.append(
            {
                "id": next_id,
                "source": "ocr",
                "type": "text",
                "cv_type": None,
                "bbox": t["bbox"],
                "center": t["center"],
                "size": t["size"],
                "confidence": t["confidence"],
                "text": t["text"],
                "texts": [],
                "children": [],
                "parent": None,
            }
        )
        next_id += 1

    elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    return elements


def draw_annotated_image(img_pil: Image.Image, elements: List[Dict[str, Any]]) -> Image.Image:
    out = img_pil.copy()
    draw = ImageDraw.Draw(out)
    for e in elements:
        b = e["bbox"]
        color = (255, 60, 60) if e["source"] == "cv" else (40, 200, 40)
        draw.rectangle(b, outline=color, width=2)
        tag = f"#{e['id']} {e['type']}"
        if e.get("text"):
            tag += f": {e['text'][:15]}"
        draw.rectangle([b[0], max(0, b[1] - 16), b[0] + len(tag) * 7 + 4, b[1]], fill=color)
        draw.text((b[0] + 3, max(0, b[1] - 15)), tag, fill=(255, 255, 255))
    return out


# Decorate with ZeroGPU allocation: attaches H100 GPU dynamically for up to 60s
@spaces.GPU(duration=60)
def segment_ui(
    image: Image.Image,
    conf: float = 0.05,
    iou: float = 0.1,
    min_conf: float = 0.3,
    contain: float = 0.6,
) -> Tuple[Dict[str, Any], Image.Image]:
    """Runs OmniParser v2 + EasyOCR on ZeroGPU (H100) and returns structured JSON + annotated image."""
    if image is None:
        return {}, None

    img_rgb = image.convert("RGB")
    w, h = img_rgb.size
    img_np = np.array(img_rgb)

    yolo_model, ocr_reader = get_models()
    import torch

    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    # 1. YOLO OmniParser detection
    res = yolo_model.predict(
        img_np, conf=conf, iou=iou, imgsz=1280, device=device, verbose=False
    )[0]
    names = res.names
    cv_elements = []
    for box in res.boxes:
        x1, y1, x2, y2 = (int(round(v)) for v in box.xyxy[0].tolist())
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        cls_idx = int(box.cls[0])
        cv_elements.append(
            {
                "type": names.get(cls_idx, str(cls_idx)),
                "bbox": [x1, y1, x2, y2],
                "confidence": round(float(box.conf[0]), 3),
            }
        )

    cv_elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
    for i, e in enumerate(cv_elements):
        e["id"] = i
        add_location_metadata(e, w, h)

    # 2. EasyOCR
    ocr_results = ocr_reader.readtext(img_np, paragraph=False)
    texts = []
    for pts, text_str, ocr_conf in ocr_results:
        text_str = text_str.strip()
        if ocr_conf < min_conf or not text_str:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x1, y1 = max(0, int(min(xs))), max(0, int(min(ys)))
        x2, y2 = min(w, int(max(xs))), min(h, int(max(ys)))
        texts.append(
            {
                "type": "text",
                "text": text_str,
                "bbox": [x1, y1, x2, y2],
                "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)],
                "size": [x2 - x1, y2 - y1],
                "confidence": round(float(ocr_conf), 3),
            }
        )
    texts.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    for i, t in enumerate(texts):
        t["id"] = i

    # 3. Geometric Merge
    combined = merge_hierarchy_and_text(cv_elements, texts, contain=contain)

    # 4. Annotated Image
    annotated = draw_annotated_image(img_rgb, combined)

    result_json = {
        "width": w,
        "height": h,
        "elements": cv_elements,
        "texts": texts,
        "combined": combined,
    }
    return json.dumps(result_json, indent=2), annotated


# Build Gradio UI + API
with gr.Blocks(title="UI Segmentation - ZeroGPU H100") as demo:
    gr.Markdown("# 📱 Mobile UI Segmentation on ZeroGPU (NVIDIA H100)")
    gr.Markdown(
        "Powered by Microsoft OmniParser v2 + EasyOCR with geometric hierarchy merge. "
        "Provides 24/7 cloud GPU acceleration with zero credit/debit card required."
    )

    with gr.Row():
        with gr.Column(scale=1):
            input_image = gr.Image(type="pil", label="Input Screenshot")
            with gr.Accordion("Detection Parameters", open=False):
                conf_slider = gr.Slider(0.01, 0.5, value=0.05, step=0.01, label="YOLO Confidence")
                iou_slider = gr.Slider(0.05, 0.5, value=0.1, step=0.01, label="NMS IoU")
                min_conf_slider = gr.Slider(0.1, 0.9, value=0.3, step=0.05, label="OCR Min Conf")
                contain_slider = gr.Slider(0.3, 0.9, value=0.6, step=0.05, label="Containment Frac")
            submit_btn = gr.Button("Segment Screen", variant="primary")

        with gr.Column(scale=1):
            annotated_out = gr.Image(type="pil", label="Visual Detection Overlay")
            json_out = gr.Code(label="Structured UI Elements (Teachable-Voice-Automation format)", language="json")

    # Expose the API endpoint for programmatic parallel calls
    submit_btn.click(
        fn=segment_ui,
        inputs=[input_image, conf_slider, iou_slider, min_conf_slider, contain_slider],
        outputs=[json_out, annotated_out],
        api_name="segment_ui",
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
