"""Easy Python Interface for Cloud GPU UI Segmentation.

One-line functions to send screenshots or video frames to your Cloud GPU (ZeroGPU H100
or LitServe) without managing low-level network connections or concurrency.

Examples:
    import cloud_gpu_ui_segmentation as cgpu

    # 1. Segment a single frame
    result = cgpu.segment_frame("screenshot.png")
    print(f"Found {len(result['combined'])} UI elements!")

    # 2. Segment a list of frames in parallel
    summary = cgpu.segment_frames_batch(["frame_1.png", "frame_2.png"], out_dir="./output")

    # 3. Segment an entire mobile screen recording (.mp4)
    summary = cgpu.segment_video("recording.mp4", sample_fps=1.0, out_dir="./output")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .parallel_client import CloudSegmenterClient
from .video_extractor import FrameExtractor

log = logging.getLogger("cloud_gpu_api")

# Default permanently hosted ZeroGPU Space on Hugging Face
DEFAULT_HF_SPACE = "ScriptBoyDK7/ui-segmentation"


def segment_frame(
    image: Union[str, Path],
    hf_space: Optional[str] = DEFAULT_HF_SPACE,
    endpoint: Optional[str] = None,
    hf_token: Optional[str] = None,
    conf: float = 0.05,
    iou: float = 0.1,
    min_conf: float = 0.3,
    contain: float = 0.6,
    out_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Segment a single screenshot/frame on Cloud GPU.

    Args:
        image: Path to image file (.png, .jpg, .jpeg)
        hf_space: Hugging Face Space ID (defaults to ScriptBoyDK7/ui-segmentation)
        endpoint: Alternative REST endpoint URL (e.g. http://127.0.0.1:8000)
        hf_token: Optional Hugging Face token for private spaces
        conf: YOLO UI element confidence threshold (default: 0.05)
        iou: NMS IoU threshold (default: 0.1)
        min_conf: EasyOCR confidence threshold (default: 0.3)
        contain: Containment fraction for text-to-element merge (default: 0.6)
        out_dir: Optional folder to save <stem>.json, <stem>_ocr.json, <stem>_combined.json, and <stem>_annotated.png

    Returns:
        Dict with 'elements', 'texts', 'combined', 'width', 'height'
    """
    img_path = Path(image)
    if not img_path.is_file():
        raise FileNotFoundError(f"Image file not found: {img_path}")

    # Use a temporary directory if no out_dir is provided
    save_to_disk = out_dir is not None
    target_dir = Path(out_dir) if out_dir else Path("temp_segment_out")
    target_dir.mkdir(parents=True, exist_ok=True)

    client = CloudSegmenterClient(
        endpoint=endpoint if not hf_space else None,
        hf_space=hf_space if not endpoint else None,
        hf_token=hf_token,
        concurrency=1,
    )

    client._process_single_frame(
        frame_path=img_path,
        out_dir=target_dir,
        conf=conf,
        iou=iou,
        min_conf=min_conf,
        contain=contain,
        return_annotated=True,
    )

    combined_file = target_dir / f"{img_path.stem}_combined.json"
    result = json.loads(combined_file.read_text(encoding="utf-8"))

    if not save_to_disk:
        # Clean up temporary directory
        import shutil

        shutil.rmtree(target_dir, ignore_errors=True)

    return result


def segment_frames_batch(
    images: List[Union[str, Path]],
    out_dir: Union[str, Path] = "segment_out",
    hf_space: Optional[str] = DEFAULT_HF_SPACE,
    endpoint: Optional[str] = None,
    hf_token: Optional[str] = None,
    concurrency: int = 4,
    conf: float = 0.05,
    iou: float = 0.1,
    min_conf: float = 0.3,
    contain: float = 0.6,
) -> Dict[str, Any]:
    """Segment a list of frames in parallel using Cloud GPUs.

    Writes identical outputs to Teachable-Voice-Automation:
        - <out_dir>/<stem>.json
        - <out_dir>/<stem>_ocr.json
        - <out_dir>/<stem>_combined.json
        - <out_dir>/<stem>_annotated.png
        - <out_dir>/benchmark_summary.json

    Returns:
        Benchmark summary dict with throughput, latency, and speedup factor.
    """
    paths = [Path(p) for p in images]
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"Frame not found: {p}")

    client = CloudSegmenterClient(
        endpoint=endpoint if not hf_space else None,
        hf_space=hf_space if not endpoint else None,
        hf_token=hf_token,
        concurrency=concurrency,
    )

    return client.process_frames_parallel(
        frame_paths=paths,
        out_dir=out_dir,
        conf=conf,
        iou=iou,
        min_conf=min_conf,
        contain=contain,
    )


def segment_video(
    video_path: Union[str, Path],
    out_dir: Union[str, Path] = "runs/cloud_segmented",
    sample_fps: float = 1.0,
    max_frames: Optional[int] = None,
    hf_space: Optional[str] = DEFAULT_HF_SPACE,
    endpoint: Optional[str] = None,
    hf_token: Optional[str] = None,
    concurrency: int = 4,
    conf: float = 0.05,
    iou: float = 0.1,
    min_conf: float = 0.3,
    contain: float = 0.6,
) -> Dict[str, Any]:
    """Extract frames from an Android screen recording (.mp4) and segment them in parallel on Cloud GPU.

    Args:
        video_path: Path to screen recording (.mp4)
        out_dir: Destination folder for frames and segmentation results
        sample_fps: Sampling rate (e.g. 1.0 = 1 frame per second)
        max_frames: Optional limit on total frames to process
        hf_space: Hugging Face ZeroGPU Space ID
        endpoint: Alternative REST endpoint
        concurrency: Number of concurrent frame streams (default: 4)

    Returns:
        Benchmark summary dict with detailed execution metrics.
    """
    out_path = Path(out_dir)
    frames_dir = out_path / "frames"
    segment_out_dir = out_path / "segment_out"
    frames_dir.mkdir(parents=True, exist_ok=True)
    segment_out_dir.mkdir(parents=True, exist_ok=True)

    extractor = FrameExtractor(video_path)
    frames_meta = extractor.extract_frames(frames_dir, sample_fps=sample_fps, max_frames=max_frames)
    frame_paths = [Path(m["file_path"]) for m in frames_meta]

    log.info(f"Extracted {len(frame_paths)} frames. Starting Cloud GPU parallel segmentation...")

    client = CloudSegmenterClient(
        endpoint=endpoint if not hf_space else None,
        hf_space=hf_space if not endpoint else None,
        hf_token=hf_token,
        concurrency=concurrency,
    )

    return client.process_frames_parallel(
        frame_paths=frame_paths,
        out_dir=segment_out_dir,
        conf=conf,
        iou=iou,
        min_conf=min_conf,
        contain=contain,
    )
