"""Provider: run the existing pipeline in this process.

Imports segment_ui.py / ocr_text.py / combine_results.py from LOCAL_SEGMENTATION_DIR and calls
their in-memory functions (detect, add_location, run_ocr, combine), so there is one copy of the
segmentation logic. Needs ultralytics + easyocr (requirements-local-cv.txt) and ~2-3 GB RAM.
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

from .base import RawSegmentation, SegmentationError


class LocalProvider:
    name = "local"

    def __init__(self, scripts_dir: str, device: str, conf: float, iou: float, min_conf: float, contain: float):
        self.scripts_dir = Path(scripts_dir).resolve()
        self.device = device or None
        self.conf, self.iou, self.min_conf, self.contain = conf, iou, min_conf, contain
        self._mods = None
        self._model = None
        self._reader = None
        self._load_lock = threading.Lock()
        # YOLO/EasyOCR objects are not safe to share across concurrent calls.
        self._run_lock = threading.Lock()

    def status(self) -> str:
        if not (self.scripts_dir / "segment_ui.py").is_file():
            return f"not_configured (segment_ui.py not found in {self.scripts_dir})"
        return "configured" + (" (models loaded)" if self._model is not None else " (models load on first call)")

    def _load(self):
        with self._load_lock:
            if self._mods is not None:
                return
            if not (self.scripts_dir / "segment_ui.py").is_file():
                raise SegmentationError(f"segment_ui.py not found in {self.scripts_dir}")
            if str(self.scripts_dir) not in sys.path:
                sys.path.insert(0, str(self.scripts_dir))
            try:
                seg = importlib.import_module("segment_ui")
                ocr = importlib.import_module("ocr_text")
                comb = importlib.import_module("combine_results")
                import easyocr
            except ImportError as exc:
                raise SegmentationError(f"local segmentation dependencies missing: {exc}") from exc
            self._model = seg.load_model(None)
            self._reader = easyocr.Reader(["en"], gpu=bool(self.device and self.device != "cpu"))
            self._mods = (seg, ocr, comb)

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        self._load()
        import cv2
        import numpy as np

        seg, ocr, comb = self._mods
        img = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise SegmentationError("could not decode image")
        h, w = img.shape[:2]
        with self._run_lock:
            # Same steps as segment_ui.segment_image(), minus writing files.
            elements, _masks = seg.detect(self._model, img, self.conf, self.iou, 1280, self.device)
            elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
            for i, e in enumerate(elements):
                e["id"] = i
                seg.add_location(e, w, h)
            texts = ocr.run_ocr(self._reader, img, self.min_conf)
        combined = comb.combine(elements, texts, self.contain)
        return RawSegmentation(w, h, combined)
