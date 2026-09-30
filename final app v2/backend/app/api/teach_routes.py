"""TEACH from a phone recording.

  POST /teach/recording            multipart: video (.mp4), tap_log (.json), plan (JSON string)
                                   -> 202 {job_id, flow_id, status_url}
  GET  /teach/jobs/{job_id}        status / stage / error; result when done:
                                   {flow, grounded_flow, summary, teach_warnings, llm_used}
  GET  /teach/jobs/{job_id}/files/{path}   any run file (frames, annotated segmentation, grounded_flow.json)

``plan`` is the assistant's confirmed TEACH plan: flow_id, app_name, summary, slots.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from ..container import Container, get_container
from ..errors import ApiError, ErrorCode

router = APIRouter(prefix="/teach", tags=["teach"])
JOB_ID_RE = re.compile(r"[0-9a-f]{12}")


def _save_upload(upload: UploadFile, dest: Path, limit_mb: int) -> Path:
    limit, written = limit_mb * 1024 * 1024, 0
    with dest.open("wb") as f:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise ApiError(ErrorCode.INVALID_REQUEST, f"{dest.name} exceeds {limit_mb} MB", status=413)
            f.write(chunk)
    if not written:
        raise ApiError(ErrorCode.INVALID_REQUEST, f"{dest.name} is empty", status=400)
    return dest


def _safe_name(name: str | None, default: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or default).name) or default


def _view(job: dict[str, Any], include_result: bool = True) -> dict[str, Any]:
    v = {k: val for k, val in job.items() if k != "result"}
    v["status_url"] = f"/teach/jobs/{job['job_id']}"
    if include_result and job.get("result"):
        v["result"] = job["result"]
    return v


@router.post("/recording", status_code=202)
async def upload_recording(video: UploadFile = File(...), tap_log: UploadFile = File(...),
                           plan: str = Form("{}"), c: Container = Depends(get_container)):
    try:
        plan_obj = json.loads(plan or "{}")
        if not isinstance(plan_obj, dict):
            raise ValueError("plan must be a JSON object")
    except ValueError as exc:
        raise ApiError(ErrorCode.INVALID_REQUEST, f"invalid plan: {exc}", status=422) from exc
    jobs = c.teach_jobs
    job_id, run_dir = jobs.new_job()
    try:
        limit = c.settings.max_video_mb
        v = await run_in_threadpool(_save_upload, video, run_dir / "input" / _safe_name(video.filename, "video.mp4"), limit)
        t = await run_in_threadpool(_save_upload, tap_log, run_dir / "input" / _safe_name(tap_log.filename, "tap_log.json"), 20)
        (run_dir / "input" / "plan.json").write_text(json.dumps(plan_obj, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    jobs.submit(job_id, v, t, plan_obj)
    return JSONResponse(_view(jobs.get(job_id)), status_code=202)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, include_result: bool = True, c: Container = Depends(get_container)) -> dict[str, Any]:
    job = c.teach_jobs.get(job_id) if JOB_ID_RE.fullmatch(job_id) else None
    if not job:
        raise ApiError(ErrorCode.INVALID_REQUEST, "teach job not found", status=404)
    return _view(job, include_result)


@router.get("/jobs/{job_id}/files/{file_path:path}")
def get_job_file(job_id: str, file_path: str, c: Container = Depends(get_container)) -> FileResponse:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ApiError(ErrorCode.INVALID_REQUEST, "teach job not found", status=404)
    run_dir = c.teach_jobs.run_dir(job_id).resolve()
    target = (run_dir / file_path).resolve()
    if run_dir not in target.parents or not target.is_file():
        raise ApiError(ErrorCode.INVALID_REQUEST, "file not found", status=404)
    return FileResponse(target)
