"""Extracts frames from mobile screen recordings (.mp4) with timestamps.

Supports multiple decoding engines (OpenCV, PyAV, or image sequence) with
graceful fallbacks and synthetic video generator for tests.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import numpy as np
from PIL import Image

log = logging.getLogger("video_extractor")


class FrameExtractor:
    """Extracts frames from video files with exact millisecond timestamps."""

    def __init__(self, video_path: str | Path):
        self.video_path = Path(video_path)
        if not self.video_path.is_file():
            raise FileNotFoundError(f"Video file not found: {self.video_path}")

    def extract_frames(
        self,
        out_dir: str | Path,
        sample_fps: Optional[float] = None,
        max_frames: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Extract frames to ``out_dir``, returning metadata list."""
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        frames_meta: List[Dict[str, Any]] = []

        # Try OpenCV first
        try:
            import cv2

            return self._extract_opencv(out_path, sample_fps, max_frames)
        except ImportError:
            pass

        # Try PyAV second
        try:
            import av

            return self._extract_pyav(out_path, sample_fps, max_frames)
        except ImportError:
            pass

        raise RuntimeError(
            "Neither OpenCV (`opencv-python`) nor PyAV (`av`) is installed. "
            "Please install one of them to extract video frames, or pass a directory of images with --input-frames."
        )

    def _extract_opencv(
        self,
        out_dir: Path,
        sample_fps: Optional[float],
        max_frames: Optional[int],
    ) -> List[Dict[str, Any]]:
        import cv2

        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV could not open video: {self.video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        log.info(f"Opened video via OpenCV: {total_frames} frames @ {fps:.2f} FPS")

        step = 1
        if sample_fps and sample_fps < fps:
            step = max(1, int(round(fps / sample_fps)))

        frame_idx = 0
        saved_idx = 0
        meta = []

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % step == 0:
                msec = cap.get(cv2.CAP_PROP_POS_MSEC)
                if msec == 0 and frame_idx > 0:
                    msec = (frame_idx / fps) * 1000.0

                frame_name = f"frame_{saved_idx + 1:04d}.png"
                frame_file = out_dir / frame_name

                # Convert BGR to RGB for PIL save
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(frame_file)

                meta.append(
                    {
                        "index": saved_idx,
                        "frame_number": frame_idx,
                        "timestamp_ms": round(msec, 2),
                        "file_path": str(frame_file.resolve()),
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                    }
                )
                saved_idx += 1
                if max_frames and saved_idx >= max_frames:
                    break

            frame_idx += 1

        cap.release()
        log.info(f"Extracted {saved_idx} frames to {out_dir}")
        return meta

    def _extract_pyav(
        self,
        out_dir: Path,
        sample_fps: Optional[float],
        max_frames: Optional[int],
    ) -> List[Dict[str, Any]]:
        import av

        container = av.open(str(self.video_path))
        stream = container.streams.video[0]
        fps = float(stream.average_rate or 30.0)

        step = 1
        if sample_fps and sample_fps < fps:
            step = max(1, int(round(fps / sample_fps)))

        meta = []
        saved_idx = 0

        for frame_idx, frame in enumerate(container.decode(stream)):
            if frame_idx % step == 0:
                msec = float(frame.pts * stream.time_base * 1000.0) if frame.pts is not None else (frame_idx / fps) * 1000.0
                frame_name = f"frame_{saved_idx + 1:04d}.png"
                frame_file = out_dir / frame_name

                img = frame.to_image()
                img.save(frame_file)

                meta.append(
                    {
                        "index": saved_idx,
                        "frame_number": frame_idx,
                        "timestamp_ms": round(msec, 2),
                        "file_path": str(frame_file.resolve()),
                        "width": img.width,
                        "height": img.height,
                    }
                )
                saved_idx += 1
                if max_frames and saved_idx >= max_frames:
                    break

        return meta


def create_synthetic_test_frames(out_dir: str | Path, count: int = 5) -> List[Path]:
    """Generate synthetic mobile UI frames for testing without any external video file."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    from PIL import ImageDraw

    created = []
    width, height = 720, 1560

    screens = [
        ("Home Screen", (245, 245, 248), "Search for restaurants...", (255, 69, 0)),
        ("Search Results", (250, 250, 250), "Biryani near me (12 results)", (70, 130, 180)),
        ("Restaurant Details", (255, 255, 255), "Royal Biryani House - 4.5 ★", (34, 139, 34)),
        ("Menu Selection", (248, 248, 250), "Chicken Dum Biryani ($14.99)", (255, 140, 0)),
        ("Checkout Screen", (255, 255, 255), "Place Order - Total $16.50", (220, 20, 60)),
    ]

    for i in range(min(count, len(screens))):
        title, bg, banner_text, action_color = screens[i]
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)

        # Top status bar
        draw.rectangle([0, 0, width, 50], fill=(230, 230, 235))
        draw.text((20, 16), "9:41", fill=(30, 30, 30))
        draw.text((width - 80, 16), "100%", fill=(30, 30, 30))

        # App bar
        draw.rectangle([0, 50, width, 140], fill=(255, 255, 255), outline=(220, 220, 220))
        draw.rectangle([20, 75, 55, 115], fill=(200, 200, 200))  # Back/menu button
        draw.text((70, 85), title, fill=(20, 20, 20))

        # Search / Input Box
        draw.rectangle([30, 160, width - 30, 225], fill=(255, 255, 255), outline=(180, 180, 180), width=2)
        draw.text((50, 182), banner_text, fill=(100, 100, 100))

        # Content Cards
        for c in range(3):
            cy = 260 + c * 200
            draw.rectangle([30, cy, width - 30, cy + 180], fill=(255, 255, 255), outline=(210, 210, 210))
            draw.rectangle([45, cy + 15, 190, cy + 165], fill=(180, 200, 220))  # Thumbnail
            draw.text((210, cy + 30), f"Item #{c + 1} - Popular Choice", fill=(30, 30, 30))
            draw.text((210, cy + 70), "Fresh ingredients, fast delivery", fill=(120, 120, 120))
            draw.rectangle([210, cy + 115, 340, cy + 155], fill=action_color)
            draw.text((230, cy + 125), "ADD +", fill=(255, 255, 255))

        # Bottom navigation bar
        draw.rectangle([0, height - 120, width, height], fill=(255, 255, 255), outline=(220, 220, 220))
        for tab in range(4):
            tx = 40 + tab * 170
            draw.rectangle([tx + 40, height - 100, tx + 80, height - 60], fill=(160, 160, 160))
            draw.text((tx + 35, height - 45), f"Tab {tab + 1}", fill=(100, 100, 100))

        frame_path = out_path / f"frame_{i + 1:04d}.png"
        img.save(frame_path)
        created.append(frame_path)

    log.info(f"Generated {len(created)} synthetic test frames in {out_dir}")
    return created
