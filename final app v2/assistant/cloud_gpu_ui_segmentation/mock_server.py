"""Lightweight mock server for testing UI segmentation client workflows locally.

Simulates the LitServe / FastAPI GPU server endpoints without requiring PyTorch,
CUDA, or OmniParser weights. Returns mock UI elements, OCR texts, and hierarchy.
Useful for rapid local testing and CI/CD verification.
"""

from __future__ import annotations

import argparse
import base64
import io
import time
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel
import uvicorn

app = FastAPI(
    title="Mock UI Segmentation Server",
    description="Simulates Cloud GPU responses for parallel testing",
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
    image: str
    conf: float = 0.05
    iou: float = 0.1
    imgsz: int = 1280
    min_conf: float = 0.3
    contain: float = 0.6
    return_annotated: bool = True


def decode_image_base64(b64_string: str) -> Image.Image:
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    image_bytes = base64.b64decode(b64_string)
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def encode_image_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_mock_detections(width: int, height: int, delay_s: float = 0.08) -> Dict[str, Any]:
    """Generate realistic mock elements and text matching Teachable-Voice-Automation schema."""
    time.sleep(delay_s)  # simulate ~80ms GPU inference

    elements = [
        {
            "id": 0,
            "type": "icon",
            "bbox": [20, 30, 80, 80],
            "confidence": 0.942,
            "center": [50, 55],
            "size": [60, 50],
            "bbox_norm": [round(20 / width, 4), round(30 / height, 4), round(80 / width, 4), round(80 / height, 4)],
            "center_norm": [round(50 / width, 4), round(55 / height, 4)],
            "area_pct": 0.35,
            "region": "top-left",
        },
        {
            "id": 1,
            "type": "container",
            "bbox": [100, 30, width - 40, 90],
            "confidence": 0.887,
            "center": [round((width + 60) / 2), 60],
            "size": [width - 140, 60],
            "bbox_norm": [round(100 / width, 4), round(30 / height, 4), round((width - 40) / width, 4), round(90 / height, 4)],
            "center_norm": [round((width + 60) / (2 * width), 4), round(60 / height, 4)],
            "area_pct": 2.1,
            "region": "top-center",
        },
        {
            "id": 2,
            "type": "button",
            "bbox": [40, 140, width - 40, 240],
            "confidence": 0.965,
            "center": [round(width / 2), 190],
            "size": [width - 80, 100],
            "bbox_norm": [round(40 / width, 4), round(140 / height, 4), round((width - 40) / width, 4), round(240 / height, 4)],
            "center_norm": [0.5, round(190 / height, 4)],
            "area_pct": 4.5,
            "region": "middle-center",
        },
    ]

    texts = [
        {
            "id": 0,
            "type": "text",
            "text": "Search restaurants or dishes",
            "bbox": [120, 45, width - 60, 75],
            "center": [round((width + 60) / 2), 60],
            "size": [width - 180, 30],
            "confidence": 0.93,
        },
        {
            "id": 1,
            "type": "text",
            "text": "Order Now - 50% OFF",
            "bbox": [60, 160, width - 60, 220],
            "center": [round(width / 2), 190],
            "size": [width - 120, 60],
            "confidence": 0.98,
        },
    ]

    combined = [
        {
            "id": 0,
            "source": "cv",
            "type": "icon_or_image",
            "cv_type": "icon",
            "bbox": [20, 30, 80, 80],
            "confidence": 0.942,
            "center": [50, 55],
            "size": [60, 50],
            "bbox_norm": [round(20 / width, 4), round(30 / height, 4), round(80 / width, 4), round(80 / height, 4)],
            "center_norm": [round(50 / width, 4), round(55 / height, 4)],
            "area_pct": 0.35,
            "region": "top-left",
            "texts": [],
            "text": "",
            "children": [],
            "parent": None,
        },
        {
            "id": 1,
            "source": "cv",
            "type": "labeled_element",
            "cv_type": "container",
            "bbox": [100, 30, width - 40, 90],
            "confidence": 0.887,
            "center": [round((width + 60) / 2), 60],
            "size": [width - 140, 60],
            "bbox_norm": [round(100 / width, 4), round(30 / height, 4), round((width - 40) / width, 4), round(90 / height, 4)],
            "center_norm": [round((width + 60) / (2 * width), 4), round(60 / height, 4)],
            "area_pct": 2.1,
            "region": "top-center",
            "texts": [texts[0]],
            "text": "Search restaurants or dishes",
            "children": [],
            "parent": None,
        },
        {
            "id": 2,
            "source": "cv",
            "type": "labeled_element",
            "cv_type": "button",
            "bbox": [40, 140, width - 40, 240],
            "confidence": 0.965,
            "center": [round(width / 2), 190],
            "size": [width - 80, 100],
            "bbox_norm": [round(40 / width, 4), round(140 / height, 4), round((width - 40) / width, 4), round(240 / height, 4)],
            "center_norm": [0.5, round(190 / height, 4)],
            "area_pct": 4.5,
            "region": "middle-center",
            "texts": [texts[1]],
            "text": "Order Now - 50% OFF",
            "children": [],
            "parent": None,
        },
    ]

    return {
        "width": width,
        "height": height,
        "elements": elements,
        "texts": texts,
        "combined": combined,
        "processing_time_s": delay_s,
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "device": "mock-gpu (NVIDIA T4 simulated)",
        "cuda_available": True,
        "gpu_name": "NVIDIA T4 (Mock Engine)",
    }


@app.post("/predict")
@app.post("/segment")
def segment_endpoint(req: SegmentRequest):
    img = decode_image_base64(req.image)
    w, h = img.size
    result = generate_mock_detections(w, h)

    if req.return_annotated:
        annotated = img.copy()
        draw = ImageDraw.Draw(annotated)
        for elem in result["combined"]:
            b = elem["bbox"]
            color = (255, 50, 50) if elem["source"] == "cv" else (50, 200, 50)
            draw.rectangle(b, outline=color, width=2)
            draw.text((b[0] + 4, max(0, b[1] - 12)), f"#{elem['id']} {elem['type']}", fill=color)
        result["annotated_image"] = encode_image_base64(annotated)
    else:
        result["annotated_image"] = None

    return result


def main():
    parser = argparse.ArgumentParser(description="Run Mock UI Segmentation Server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
