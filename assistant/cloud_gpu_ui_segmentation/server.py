"""High-performance Cloud GPU UI Segmentation Server using LitServe / FastAPI.

Optimized for deployment on Lightning AI Studio (T4 GPU), Google Colab, or any
cloud GPU server. Executes Microsoft OmniParser v2 (YOLOv8 icon_detect) and EasyOCR
in batch mode on CUDA, then applies geometric hierarchy combining in memory.

Run on Lightning AI Studio:
    pip install -r requirements-server.txt
    python server.py --port 8000
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ui_segmentation_server")

# Hugging Face OmniParser v2 icon detector weights
HF_REPO = "microsoft/OmniParser-v2.0"
HF_FILE = "icon_detect/model.pt"


def decode_image_from_base64(b64_string: str) -> np.ndarray:
    """Decode a base64 encoded image string to an RGB numpy array."""
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    image_bytes = base64.b64decode(b64_string)
    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(pil_img)


def encode_image_to_base64(image_np: np.ndarray, format: str = "PNG") -> str:
    """Encode an RGB numpy array image to base64 string."""
    pil_img = Image.fromarray(image_np.astype(np.uint8))
    buffer = io.BytesIO()
    pil_img.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def add_location_metadata(e: Dict[str, Any], w: int, h: int) -> None:
    """Add pixel and normalized geometry plus coarse 3x3 region to an element."""
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


def compute_area(b: List[int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def inside_fraction(inner: List[int], outer: List[int]) -> float:
    """Calculate the fraction of the inner box contained within the outer box."""
    x1, y1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x2, y2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    return overlap / (compute_area(inner) + 1e-9)


def merge_hierarchy_and_text(
    cv_elements: List[Dict[str, Any]],
    texts: List[Dict[str, Any]],
    contain: float = 0.6,
) -> List[Dict[str, Any]]:
    """Join CV bounding boxes and OCR text boxes with containment hierarchy."""
    elements = [dict(e, source="cv", texts=[], children=[], parent=None) for e in cv_elements]

    # 1. Hierarchy: parent is smallest other CV element containing >= 90%
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

    # 2. Assign OCR text boxes to smallest containing CV element
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

    # 3. Classify element types and join text strings
    for e in elements:
        e["texts"].sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
        e["text"] = " ".join(t["text"] for t in e["texts"])
        e["cv_type"] = e.pop("type", "icon")
        e["type"] = (
            "container" if e["children"] else ("labeled_element" if e["texts"] else "icon_or_image")
        )

    # 4. Include unmatched OCR boxes as standalone text elements
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


def draw_annotated_image(img_rgb: np.ndarray, combined_elements: List[Dict[str, Any]]) -> np.ndarray:
    """Draw bounding boxes and ID labels on the image for visual verification."""
    from PIL import ImageDraw, ImageFont

    pil_img = Image.fromarray(img_rgb.copy())
    draw = ImageDraw.Draw(pil_img)

    for e in combined_elements:
        x1, y1, x2, y2 = e["bbox"]
        color = (255, 60, 60) if e["source"] == "cv" else (40, 200, 40)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)

        # Draw text label box
        tag = f"#{e['id']} {e['type']}"
        if e.get("text"):
            tag += f": {e['text'][:15]}"
        draw.rectangle([x1, max(0, y1 - 16), x1 + len(tag) * 7 + 6, y1], fill=color)
        draw.text((x1 + 3, max(0, y1 - 15)), tag, fill=(255, 255, 255))

    return np.array(pil_img)


class UISegmenterPipeline:
    """Loads models and executes UI element detection + OCR + geometric merging."""

    def __init__(self, device: str = "auto", weights_path: Optional[str] = None):
        import torch

        if device == "auto":
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        log.info(f"Initializing UISegmenterPipeline on device: {self.device}")

        # 1. Load YOLOv8 OmniParser detector
        self.yolo_model = self._load_yolo_model(weights_path)

        # 2. Load EasyOCR
        import easyocr

        use_gpu = "cuda" in self.device
        log.info(f"Loading EasyOCR (gpu={use_gpu})...")
        self.ocr_reader = easyocr.Reader(["en"], gpu=use_gpu)
        log.info("Models loaded and ready for inference!")

    def _load_yolo_model(self, weights_path: Optional[str]):
        from ultralytics import YOLO

        if weights_path is None:
            from huggingface_hub import hf_hub_download

            log.info(f"Downloading/verifying OmniParser v2 weights from {HF_REPO}:{HF_FILE}...")
            weights_path = hf_hub_download(HF_REPO, HF_FILE)
            log.info(f"Using weights at: {weights_path}")
        return YOLO(weights_path)

    def process_image(
        self,
        img_rgb: np.ndarray,
        conf: float = 0.05,
        iou: float = 0.1,
        imgsz: int = 1280,
        min_conf_ocr: float = 0.3,
        contain: float = 0.6,
        return_annotated: bool = True,
    ) -> Dict[str, Any]:
        """Process a single image through detection, OCR, and hierarchy combine."""
        t0 = time.perf_counter()
        h, w = img_rgb.shape[:2]

        # 1. Run YOLO OmniParser detection
        res = self.yolo_model.predict(
            img_rgb, conf=conf, iou=iou, imgsz=imgsz, device=self.device, verbose=False
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

        # Sort top-to-bottom, left-to-right
        cv_elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        for i, e in enumerate(cv_elements):
            e["id"] = i
            add_location_metadata(e, w, h)

        # 2. Run EasyOCR
        ocr_results = self.ocr_reader.readtext(img_rgb, paragraph=False)
        texts = []
        for pts, text_str, ocr_conf in ocr_results:
            text_str = text_str.strip()
            if ocr_conf < min_conf_ocr or not text_str:
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

        # 4. Optional annotated image
        annotated_b64 = None
        if return_annotated:
            annotated_np = draw_annotated_image(img_rgb, combined)
            annotated_b64 = encode_image_to_base64(annotated_np)

        elapsed = round(time.perf_counter() - t0, 3)

        return {
            "width": w,
            "height": h,
            "elements": cv_elements,
            "texts": texts,
            "combined": combined,
            "annotated_image": annotated_b64,
            "processing_time_s": elapsed,
        }


# ==============================================================================
# Serving Infrastructure (LitServe with fallback to FastAPI)
# ==============================================================================

def create_fastapi_app(pipeline: UISegmenterPipeline):
    """Create a FastAPI application hosting the UI segmentation pipeline."""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    app = FastAPI(
        title="Cloud GPU UI Segmentation API",
        description="Parallel UI element segmentation for screen recordings powered by OmniParser v2 + EasyOCR",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class SegmentRequest(BaseModel):
        image: str  # base64 encoded image
        conf: float = 0.05
        iou: float = 0.1
        imgsz: int = 1280
        min_conf: float = 0.3
        contain: float = 0.6
        return_annotated: bool = True

    class BatchSegmentRequest(BaseModel):
        images: List[str]  # list of base64 encoded images
        conf: float = 0.05
        iou: float = 0.1
        imgsz: int = 1280
        min_conf: float = 0.3
        contain: float = 0.6
        return_annotated: bool = True

    @app.get("/health")
    def health_check():
        import torch

        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None (CPU)"
        return {
            "status": "healthy",
            "device": pipeline.device,
            "cuda_available": torch.cuda.is_available(),
            "gpu_name": gpu_name,
        }

    @app.post("/predict")
    @app.post("/segment")
    def segment_single(req: SegmentRequest):
        try:
            img = decode_image_from_base64(req.image)
            return pipeline.process_image(
                img,
                conf=req.conf,
                iou=req.iou,
                imgsz=req.imgsz,
                min_conf_ocr=req.min_conf,
                contain=req.contain,
                return_annotated=req.return_annotated,
            )
        except Exception as exc:
            log.exception("Segmentation failed")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/segment_batch")
    def segment_batch(req: BatchSegmentRequest):
        results = []
        for b64 in req.images:
            img = decode_image_from_base64(b64)
            res = pipeline.process_image(
                img,
                conf=req.conf,
                iou=req.iou,
                imgsz=req.imgsz,
                min_conf_ocr=req.min_conf,
                contain=req.contain,
                return_annotated=req.return_annotated,
            )
            results.append(res)
        return {"batch_size": len(results), "results": results}

    return app


def run_litserve_server(pipeline: UISegmenterPipeline, port: int = 8000):
    """Run via LitServe (Lightning AI official serving engine with auto-batching)."""
    try:
        import litserve as ls
    except ImportError:
        log.warning("LitServe not installed. Falling back to FastAPI + Uvicorn.")
        return False

    class UISegmentLitAPI(ls.LitAPI):
        def setup(self, device):
            self.pipeline = pipeline

        def decode_request(self, request: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, Any]]:
            img = decode_image_from_base64(request["image"])
            params = {
                "conf": request.get("conf", 0.05),
                "iou": request.get("iou", 0.1),
                "imgsz": request.get("imgsz", 1280),
                "min_conf": request.get("min_conf", 0.3),
                "contain": request.get("contain", 0.6),
                "return_annotated": request.get("return_annotated", True),
            }
            return img, params

        def predict(self, x: Tuple[np.ndarray, Dict[str, Any]]) -> Dict[str, Any]:
            img, params = x
            return self.pipeline.process_image(
                img,
                conf=params["conf"],
                iou=params["iou"],
                imgsz=params["imgsz"],
                min_conf_ocr=params["min_conf"],
                contain=params["contain"],
                return_annotated=params["return_annotated"],
            )

        def encode_response(self, output: Dict[str, Any]) -> Dict[str, Any]:
            return output

    log.info(f"Starting LitServe engine on port {port} with auto accelerator...")
    server = ls.LitServer(
        UISegmentLitAPI(),
        accelerator="auto",
        max_batch_size=8,
        batch_timeout=0.04,
    )
    server.run(port=port)
    return True


def main():
    parser = argparse.ArgumentParser(description="Cloud GPU UI Segmentation Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on (default: 8000)")
    parser.add_argument("--device", default="auto", help="Compute device ('auto', 'cuda:0', 'cpu')")
    parser.add_argument("--weights", default=None, help="Path to local OmniParser weights .pt file")
    parser.add_argument(
        "--fastapi-only", action="store_true", help="Force pure FastAPI instead of LitServe"
    )
    args = parser.parse_args()

    pipeline = UISegmenterPipeline(device=args.device, weights_path=args.weights)

    if not args.fastapi_only:
        started_litserve = run_litserve_server(pipeline, port=args.port)
        if started_litserve:
            return

    # Fallback to FastAPI + Uvicorn
    import uvicorn

    app = create_fastapi_app(pipeline)
    log.info(f"Starting FastAPI server on http://0.0.0.0:{args.port}...")
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
