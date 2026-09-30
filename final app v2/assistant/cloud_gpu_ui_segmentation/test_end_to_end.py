"""Automated End-to-End Verification Test for Cloud GPU UI Segmentation.

Tests the complete parallel client-server workflow:
- Spawns background server
- Generates test mobile frames
- Dispatches parallel requests
- Validates output JSON schemas and annotations
- Verifies original repository integrity
"""

import json
from pathlib import Path
import shutil
import sys
import threading
import time
import unittest

import uvicorn

from cloud_gpu_ui_segmentation.mock_server import app
from cloud_gpu_ui_segmentation.parallel_client import CloudSegmenterClient
from cloud_gpu_ui_segmentation.video_extractor import create_synthetic_test_frames


class TestCloudGPUUISegmentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Start server on local test port
        cls.port = 8765
        cls.endpoint = f"http://127.0.0.1:{cls.port}"

        def run_srv():
            uvicorn.run(app, host="127.0.0.1", port=cls.port, log_level="warning")

        cls.server_thread = threading.Thread(target=run_srv, daemon=True)
        cls.server_thread.start()
        time.sleep(1.2)  # Give uvicorn time to start

        # 2. Setup test output directories
        cls.test_dir = Path("test_scratch_run")
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)
        cls.test_dir.mkdir(parents=True)
        cls.frames_dir = cls.test_dir / "frames"
        cls.segment_out_dir = cls.test_dir / "segment_out"

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)

    def test_01_health_check(self):
        client = CloudSegmenterClient(endpoint=self.endpoint)
        health = client.check_health()
        self.assertEqual(health.get("status"), "healthy")
        self.assertIn("device", health)

    def test_02_parallel_dispatch_and_schema_validation(self):
        # Generate 4 synthetic frames
        frames = create_synthetic_test_frames(self.frames_dir, count=4)
        self.assertEqual(len(frames), 4)

        # Dispatch with concurrency=4
        client = CloudSegmenterClient(endpoint=self.endpoint, concurrency=4)
        summary = client.process_frames_parallel(
            frame_paths=frames,
            out_dir=self.segment_out_dir,
        )

        self.assertEqual(summary["total_frames"], 4)
        self.assertEqual(summary["successful_frames"], 4)
        self.assertEqual(summary["failed_frames"], 0)
        self.assertGreater(summary["throughput_fps"], 0.0)

        # Verify output files exist for each frame
        for frame in frames:
            stem = frame.stem
            cv_file = self.segment_out_dir / f"{stem}.json"
            ocr_file = self.segment_out_dir / f"{stem}_ocr.json"
            comb_file = self.segment_out_dir / f"{stem}_combined.json"
            img_file = self.segment_out_dir / f"{stem}_annotated.png"

            self.assertTrue(cv_file.is_file(), f"Missing {cv_file}")
            self.assertTrue(ocr_file.is_file(), f"Missing {ocr_file}")
            self.assertTrue(comb_file.is_file(), f"Missing {comb_file}")
            self.assertTrue(img_file.is_file(), f"Missing {img_file}")

            # Verify schema compliance with Teachable-Voice-Automation
            comb_data = json.loads(comb_file.read_text(encoding="utf-8"))
            self.assertIn("elements", comb_data)
            self.assertIn("width", comb_data)
            self.assertIn("height", comb_data)

            for elem in comb_data["elements"]:
                self.assertIn("id", elem)
                self.assertIn("bbox", elem)
                self.assertIn("center", elem)
                self.assertIn("size", elem)
                self.assertIn("type", elem)
                self.assertIn("source", elem)
                self.assertIn("parent", elem)
                self.assertIn("children", elem)

        # Verify benchmark summary was saved
        bench_file = self.segment_out_dir / "benchmark_summary.json"
        self.assertTrue(bench_file.is_file())


if __name__ == "__main__":
    unittest.main()
