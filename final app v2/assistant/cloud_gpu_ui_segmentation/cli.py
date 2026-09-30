"""Unified Standalone CLI for Cloud GPU Parallel UI Segmentation.

Handles video frame extraction, parallel GPU dispatching via LitServe/FastAPI,
and outputs structured JSON and annotated PNGs identical to Teachable-Voice-Automation.

Usage:
    # 1. Using a remote Lightning AI Studio endpoint:
    python -m cloud_gpu_ui_segmentation.cli --input screen_recording.mp4 --endpoint https://8000-xxxx.lightning.ai

    # 2. Testing locally with the built-in mock server (zero setup):
    python -m cloud_gpu_ui_segmentation.cli --demo --mock

    # 3. Processing an existing directory of screenshots:
    python -m cloud_gpu_ui_segmentation.cli --input-frames ./screenshots --endpoint https://8000-xxxx.lightning.ai
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import threading
import time

from cloud_gpu_ui_segmentation.parallel_client import CloudSegmenterClient
from cloud_gpu_ui_segmentation.video_extractor import FrameExtractor, create_synthetic_test_frames

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ui_segmentation_cli")


def start_mock_server_background(port: int = 8000):
    """Start the mock server in a daemon thread for local testing."""
    import uvicorn
    from cloud_gpu_ui_segmentation.mock_server import app

    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(1.0)  # allow server to bind
    log.info(f"Local mock server started on http://127.0.0.1:{port}")


def main():
    parser = argparse.ArgumentParser(
        description="Parallel Cloud GPU UI Segmentation for Screen Recordings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input", "-i", help="Path to input screen recording (.mp4)")
    input_group.add_argument(
        "--input-frames", help="Path to directory containing screenshot frames (.png, .jpg)"
    )
    input_group.add_argument(
        "--demo", action="store_true", help="Generate synthetic mobile UI frames for a demo test"
    )

    parser.add_argument(
        "--endpoint",
        "-e",
        default="http://127.0.0.1:8000",
        help="Cloud GPU API endpoint URL (e.g. https://8000-xxxx.lightning.ai)",
    )
    parser.add_argument(
        "--hf-space",
        help="Hugging Face Space ID running ZeroGPU (e.g. username/ui-segmentation)",
    )
    parser.add_argument(
        "--hf-token",
        help="Optional Hugging Face User Access Token for private spaces or higher quota",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Launch and connect to a local mock server automatically (no GPU/cloud required)",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=8,
        help="Number of concurrent frame requests to dispatch (default: 8)",
    )
    parser.add_argument(
        "--out-dir",
        "-o",
        default="runs/cloud_segmented",
        help="Output directory (default: runs/cloud_segmented)",
    )
    parser.add_argument(
        "--sample-fps",
        type=float,
        default=1.0,
        help="Frame extraction sampling rate in frames/sec (default: 1.0)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum number of frames to extract and process",
    )
    parser.add_argument(
        "--conf", type=float, default=0.05, help="YOLO UI detection confidence threshold (default: 0.05)"
    )
    parser.add_argument("--iou", type=float, default=0.1, help="NMS IoU threshold (default: 0.1)")
    parser.add_argument(
        "--min-conf", type=float, default=0.3, help="EasyOCR minimum confidence threshold (default: 0.3)"
    )
    parser.add_argument(
        "--contain",
        type=float,
        default=0.6,
        help="Geometric containment fraction to merge OCR text into CV elements (default: 0.6)",
    )

    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    frames_dir = out_dir / "frames"
    segment_out_dir = out_dir / "segment_out"
    frames_dir.mkdir(parents=True, exist_ok=True)
    segment_out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Determine frame sources
    frame_paths = []
    if args.input:
        log.info(f"Extracting frames from video: {args.input}")
        extractor = FrameExtractor(args.input)
        frames_meta = extractor.extract_frames(
            frames_dir, sample_fps=args.sample_fps, max_frames=args.max_frames
        )
        frame_paths = [Path(m["file_path"]) for m in frames_meta]
    elif args.input_frames:
        src = Path(args.input_frames)
        if not src.is_dir():
            raise FileNotFoundError(f"Input frames directory not found: {src}")
        frame_paths = sorted(
            [p for p in src.glob("*.*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        )
        log.info(f"Found {len(frame_paths)} frames in {src}")
    elif args.demo:
        log.info("Generating synthetic mobile UI frames for demo...")
        frame_paths = create_synthetic_test_frames(frames_dir, count=args.max_frames or 5)
    else:
        # Check if there are sample frames in Teachable-Voice-Automation ground-flow
        fallback_test = Path("Teachable-Voice-Automation/ui-segmentation")
        log.info("No input specified; falling back to demo mode with synthetic UI frames.")
        frame_paths = create_synthetic_test_frames(frames_dir, count=args.max_frames or 5)

    if not frame_paths:
        log.error("No frames found to process.")
        sys.exit(1)

    # 2. Check or boot mock server if requested
    endpoint = args.endpoint
    if args.mock:
        start_mock_server_background(port=8000)
        endpoint = "http://127.0.0.1:8000"

    # 3. Initialize parallel client
    if args.hf_space:
        client = CloudSegmenterClient(
            hf_space=args.hf_space,
            hf_token=args.hf_token,
            concurrency=args.concurrency,
        )
        display_target = f"Hugging Face Space: {args.hf_space}"
    else:
        client = CloudSegmenterClient(endpoint=endpoint, concurrency=args.concurrency)
        display_target = endpoint

    health = client.check_health()
    log.info(f"Target: {display_target} | Status: {health.get('status')} | Provider: {health.get('provider')}")

    # 4. Process frames in parallel
    print("\n" + "=" * 65)
    print(f"  CLOUD GPU UI SEGMENTATION PARALLEL RUNNER")
    print(f"  Frames to Process: {len(frame_paths)}")
    print(f"  Concurrency:       {args.concurrency} workers")
    print(f"  Target:            {display_target}")
    print("=" * 65 + "\n")

    summary = client.process_frames_parallel(
        frame_paths=frame_paths,
        out_dir=segment_out_dir,
        conf=args.conf,
        iou=args.iou,
        min_conf=args.min_conf,
        contain=args.contain,
    )

    # 5. Print summary benchmark table
    print("\n" + "=" * 65)
    print("  RUN BENCHMARK SUMMARY")
    print("=" * 65)
    print(f"  Processed Frames:     {summary['successful_frames']} / {summary['total_frames']}")
    print(f"  Total Time:           {summary['total_time_seconds']} seconds")
    print(f"  Throughput:           {summary['throughput_fps']} FPS")
    print(f"  Avg Latency / Frame:  {summary['avg_latency_per_frame_s']} seconds")
    print(f"  Estimated CPU Time:   {summary['estimated_cpu_baseline_seconds']} seconds")
    print(f"  Speedup Factor:       {summary['speedup_multiplier']} FASTER than CPU")
    print(f"  Cloud Compute Cost:   $0.00 (Zero-card Free Tier)")
    print(f"  Output Directory:     {segment_out_dir.resolve()}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
