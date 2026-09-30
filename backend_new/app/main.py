"""FastAPI service for the tap-to-flow pipeline with LLM (OpenRouter) UI segmentation.

Run locally:   uvicorn app.main:app --reload            (from backend_new/)
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from . import segmentation
from .jobs import jobs
from .settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="Tap-to-flow backend",
    version="1.0.0",
    description="Screen recording + tap log -> grounded UI flow. UI segmentation and OCR are done "
                "in one vision-LLM call per frame (OpenRouter).",
)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.service_api_key and x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def _save_upload(upload: UploadFile, dest: Path) -> Path:
    """Stream an upload to disk, enforcing MAX_UPLOAD_MB."""
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with dest.open("wb") as f:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"{upload.filename} exceeds {settings.max_upload_mb} MB")
            f.write(chunk)
    if written == 0:
        raise HTTPException(status_code=400, detail=f"{upload.filename or dest.name} is empty")
    return dest


def _safe_name(name: str | None, default: str) -> str:
    name = Path(name or default).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or default


def _job_view(job: dict[str, Any], include_result: bool = True) -> dict[str, Any]:
    view = {k: v for k, v in job.items() if k != "result"}
    view["status_url"] = f"/pipeline/{job['job_id']}"
    if include_result and job.get("result"):
        view["result"] = job["result"]
    return view


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "segmentation_model": settings.seg_model,
            "openrouter_key_configured": bool(settings.openrouter_api_key)}


@app.post("/segment", dependencies=[Depends(require_api_key)])
def segment_screenshot(image: UploadFile = File(..., description="PNG/JPG screenshot")) -> dict[str, Any]:
    """Stage 3 alone: UI elements + text of one screenshot, in one model call."""
    data = image.file.read()
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="could not decode image")
    try:
        elements, info = segmentation.segment(img)
    except segmentation.SegmentationError as exc:
        raise HTTPException(status_code=502, detail=f"segmentation failed: {exc}") from exc
    h, w = img.shape[:2]
    return {"width": w, "height": h, **info, "elements": elements}


@app.post("/pipeline", dependencies=[Depends(require_api_key)], status_code=202)
async def start_pipeline(
    video: UploadFile = File(..., description="screen recording (.mp4) made with Show taps on"),
    tap_log: UploadFile = File(..., description="TapScreenRecorder tap log (.json) for the same recording"),
    wait: bool = Query(False, description="block until the job finishes and return the result"),
):
    """Start the full pipeline. Returns a job to poll at GET /pipeline/{job_id}
    (or, with ?wait=true, the finished job)."""
    job_id, run_dir = jobs.new_job_dir()
    inputs = run_dir / "input"
    inputs.mkdir()
    try:
        video_path = await run_in_threadpool(_save_upload, video, inputs / _safe_name(video.filename, "video.mp4"))
        log_path = await run_in_threadpool(_save_upload, tap_log, inputs / _safe_name(tap_log.filename, "tap_log.json"))
    except HTTPException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    jobs.submit(job_id, video_path, log_path)
    if wait:
        job = await run_in_threadpool(jobs.wait, job_id)
        return JSONResponse(_job_view(job), status_code=200 if job["status"] == "succeeded" else 500)
    return _job_view(jobs.get(job_id))


@app.get("/pipeline/{job_id}", dependencies=[Depends(require_api_key)])
def get_job(job_id: str, include_result: bool = True) -> dict[str, Any]:
    """Job status (queued / running / succeeded / failed), current stage, and the result when done."""
    job = jobs.get(job_id) if re.fullmatch(r"[0-9a-f]{12}", job_id) else None
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_view(job, include_result)


@app.get("/pipeline/{job_id}/files/{file_path:path}", dependencies=[Depends(require_api_key)])
def get_job_file(job_id: str, file_path: str) -> FileResponse:
    """Any file of a run, e.g. flow_frames/step_003_before.png or
    flow_frames/segment_out/step_003_before_combined_annotated.png. Paths match those in grounded_flow."""
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(status_code=404, detail="job not found")
    run_dir = jobs.run_dir(job_id).resolve()
    target = (run_dir / file_path).resolve()
    if run_dir not in target.parents or not target.is_file() or target.parent == run_dir / "input":
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)
