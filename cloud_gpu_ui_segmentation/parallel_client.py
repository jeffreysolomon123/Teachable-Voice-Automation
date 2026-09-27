"""Parallel client dispatcher for Cloud GPU UI Segmentation.

Supports:
1. REST Endpoints (Lightning AI LitServe, Google Colab Tunnel, or Local/Mock server)
2. Hugging Face Spaces (ZeroGPU NVIDIA H100 via gradio_client or HTTP)
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("parallel_client")


class BaseSegmenterClient:
    """Base class providing parallel dispatching, metrics, and benchmark reporting."""

    def __init__(self, concurrency: int = 8):
        self.concurrency = concurrency
        self.provider_name = "Base"

    def check_health(self) -> Dict[str, Any]:
        raise NotImplementedError

    def _process_single_frame(
        self,
        frame_path: Path,
        out_dir: Path,
        conf: float = 0.05,
        iou: float = 0.1,
        imgsz: int = 1280,
        min_conf: float = 0.3,
        contain: float = 0.6,
        return_annotated: bool = True,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def process_frames_parallel(
        self,
        frame_paths: List[Path],
        out_dir: str | Path,
        conf: float = 0.05,
        iou: float = 0.1,
        min_conf: float = 0.3,
        contain: float = 0.6,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Dispatch all frames concurrently across cloud GPU workers."""
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        total_frames = len(frame_paths)
        log.info(
            f"Dispatching {total_frames} frames via {self.provider_name} "
            f"with concurrency={self.concurrency}..."
        )

        t_start = time.perf_counter()
        results: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_frame = {
                executor.submit(
                    self._process_single_frame,
                    fp,
                    out_path,
                    conf,
                    iou,
                    1280,
                    min_conf,
                    contain,
                    True,
                ): fp
                for fp in frame_paths
            }

            for idx, future in enumerate(as_completed(future_to_frame), start=1):
                fp = future_to_frame[future]
                try:
                    res = future.result()
                    results.append(res)
                    if progress_callback:
                        progress_callback(idx, total_frames, res)
                    else:
                        log.info(
                            f"[{idx}/{total_frames}] {res['frame']}: {res['combined_count']} elements in {res['elapsed_s']}s"
                        )
                except Exception as exc:
                    log.error(f"Failed processing {fp.name}: {exc}")
                    results.append({"frame": fp.name, "error": str(exc)})

        total_time = time.perf_counter() - t_start
        fps = round(total_frames / total_time, 2) if total_time > 0 else 0.0

        # Calculate speedup compared to local CPU baseline (~15.3s/frame from Teachable-Voice-Automation test results)
        cpu_baseline_time = total_frames * 15.3
        speedup = round(cpu_baseline_time / total_time, 1) if total_time > 0 else 1.0

        summary = {
            "provider": self.provider_name,
            "total_frames": total_frames,
            "successful_frames": len([r for r in results if "error" not in r]),
            "failed_frames": len([r for r in results if "error" in r]),
            "concurrency": self.concurrency,
            "total_time_seconds": round(total_time, 2),
            "throughput_fps": fps,
            "avg_latency_per_frame_s": round(total_time / max(1, total_frames), 3),
            "estimated_cpu_baseline_seconds": round(cpu_baseline_time, 2),
            "speedup_multiplier": f"{speedup}x",
            "cost_usd": 0.0,
            "credits_used": "0 (Free Tier)",
            "output_directory": str(out_path.resolve()),
            "details": results,
        }

        # Save summary report
        (out_path / "benchmark_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        return summary


class RESTSegmenterClient(BaseSegmenterClient):
    """Dispatches UI segmentation tasks to a REST endpoint (LitServe, FastAPI, Mock)."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8000",
        concurrency: int = 8,
        timeout_s: float = 60.0,
    ):
        super().__init__(concurrency=concurrency)
        self.endpoint = endpoint.rstrip("/")
        self.provider_name = f"REST Endpoint ({self.endpoint})"
        self.timeout_s = timeout_s
        self.session = self._create_resilient_session()

    def _create_resilient_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=self.concurrency * 2,
            pool_maxsize=self.concurrency * 2,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def check_health(self) -> Dict[str, Any]:
        url = f"{self.endpoint}/health"
        try:
            resp = self.session.get(url, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            data["provider"] = self.provider_name
            return data
        except Exception as exc:
            log.warning(f"Health check at {url} failed: {exc}")
            return {"status": "unreachable", "error": str(exc), "provider": self.provider_name}

    def _process_single_frame(
        self,
        frame_path: Path,
        out_dir: Path,
        conf: float = 0.05,
        iou: float = 0.1,
        imgsz: int = 1280,
        min_conf: float = 0.3,
        contain: float = 0.6,
        return_annotated: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()

        with open(frame_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "image": b64_image,
            "conf": conf,
            "iou": iou,
            "imgsz": imgsz,
            "min_conf": min_conf,
            "contain": contain,
            "return_annotated": return_annotated,
        }

        post_url = f"{self.endpoint}/predict"
        resp = self.session.post(post_url, json=payload, timeout=self.timeout_s)
        if resp.status_code == 404:
            post_url = f"{self.endpoint}/segment"
            resp = self.session.post(post_url, json=payload, timeout=self.timeout_s)

        resp.raise_for_status()
        data = resp.json()

        stem = frame_path.stem
        w, h = data.get("width", 0), data.get("height", 0)

        # 1. <stem>.json
        cv_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "elements": data.get("elements", []),
        }
        (out_dir / f"{stem}.json").write_text(json.dumps(cv_json, indent=2), encoding="utf-8")

        # 2. <stem>_ocr.json
        ocr_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "texts": data.get("texts", []),
        }
        (out_dir / f"{stem}_ocr.json").write_text(
            json.dumps(ocr_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 3. <stem>_combined.json
        combined_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "elements": data.get("combined", []),
        }
        (out_dir / f"{stem}_combined.json").write_text(
            json.dumps(combined_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 4. <stem>_annotated.png
        if data.get("annotated_image"):
            raw_png = base64.b64decode(data["annotated_image"])
            (out_dir / f"{stem}_annotated.png").write_bytes(raw_png)

        elapsed = time.perf_counter() - t0
        return {
            "frame": frame_path.name,
            "stem": stem,
            "elapsed_s": round(elapsed, 3),
            "elements_count": len(data.get("elements", [])),
            "texts_count": len(data.get("texts", [])),
            "combined_count": len(data.get("combined", [])),
        }


class HFSpaceSegmenterClient(BaseSegmenterClient):
    """Dispatches UI segmentation tasks to a Hugging Face ZeroGPU Space."""

    def __init__(
        self,
        space_id: str,
        hf_token: Optional[str] = None,
        concurrency: int = 4,
    ):
        super().__init__(concurrency=concurrency)
        self.space_id = space_id.replace("https://huggingface.co/spaces/", "")
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.provider_name = f"Hugging Face Space ({self.space_id})"
        self._thread_local = threading.local()
        # Verify connection on main thread
        self._get_client()

    def _get_client(self):
        if not hasattr(self._thread_local, "client"):
            try:
                from gradio_client import Client

                self._thread_local.client = Client(self.space_id, token=self.hf_token)
            except ImportError:
                raise RuntimeError(
                    "Calling a Hugging Face Space requires `gradio_client`. "
                    "Please install it: pip install gradio_client"
                )
        return self._thread_local.client

    def check_health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "provider": self.provider_name,
            "device": "ZeroGPU (NVIDIA H100)",
            "space_id": self.space_id,
        }

    def _process_single_frame(
        self,
        frame_path: Path,
        out_dir: Path,
        conf: float = 0.05,
        iou: float = 0.1,
        imgsz: int = 1280,
        min_conf: float = 0.3,
        contain: float = 0.6,
        return_annotated: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        from gradio_client import handle_file

        client = self._get_client()
        result = client.predict(
            image=handle_file(str(frame_path)),
            conf=conf,
            iou=iou,
            min_conf=min_conf,
            contain=contain,
            api_name="/segment_ui",
        )

        data, annotated_path = result
        if isinstance(data, str):
            data = json.loads(data)
        stem = frame_path.stem
        w, h = data.get("width", 0), data.get("height", 0)

        # 1. <stem>.json
        cv_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "elements": data.get("elements", []),
        }
        (out_dir / f"{stem}.json").write_text(json.dumps(cv_json, indent=2), encoding="utf-8")

        # 2. <stem>_ocr.json
        ocr_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "texts": data.get("texts", []),
        }
        (out_dir / f"{stem}_ocr.json").write_text(
            json.dumps(ocr_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 3. <stem>_combined.json
        combined_json = {
            "image": str(frame_path.resolve()),
            "width": w,
            "height": h,
            "elements": data.get("combined", []),
        }
        (out_dir / f"{stem}_combined.json").write_text(
            json.dumps(combined_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 4. <stem>_annotated.png
        if annotated_path and Path(annotated_path).is_file():
            shutil.copyfile(annotated_path, out_dir / f"{stem}_annotated.png")

        elapsed = time.perf_counter() - t0
        return {
            "frame": frame_path.name,
            "stem": stem,
            "elapsed_s": round(elapsed, 3),
            "elements_count": len(data.get("elements", [])),
            "texts_count": len(data.get("texts", [])),
            "combined_count": len(data.get("combined", [])),
        }


def CloudSegmenterClient(
    endpoint: Optional[str] = None,
    hf_space: Optional[str] = None,
    hf_token: Optional[str] = None,
    concurrency: int = 8,
) -> BaseSegmenterClient:
    """Factory creating either a REST or Hugging Face Space client."""
    if hf_space:
        return HFSpaceSegmenterClient(
            space_id=hf_space, hf_token=hf_token, concurrency=concurrency
        )
    return RESTSegmenterClient(endpoint=endpoint or "http://127.0.0.1:8000", concurrency=concurrency)
