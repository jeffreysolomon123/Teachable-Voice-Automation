"""Background pipeline jobs.

Jobs run in a small thread pool (MAX_CONCURRENT_JOBS). Each job lives in RUNS_DIR/<job_id>/
and its state is mirrored to job.json there, so a finished job can still be read after a
restart as long as the disk survives (Render's disk is ephemeral unless a persistent disk is
attached). Run folders older than JOB_TTL_HOURS are deleted when a new job starts.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .pipeline import PipelineError, run_pipeline
from .settings import settings

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self) -> None:
        self.runs_dir = settings.runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs, thread_name_prefix="job")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}

    # ---------------------------------------------------------------- state

    def _save(self, job: dict[str, Any]) -> None:
        path = self.runs_dir / job["job_id"] / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields, updated_at=time.time())
            self._save(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            if job_id in self._jobs:
                return dict(self._jobs[job_id])
        path = self.run_dir(job_id) / "job.json"
        if path.is_file():  # finished before a restart
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def run_dir(self, job_id: str) -> Path:
        return self.runs_dir / job_id

    # ---------------------------------------------------------------- lifecycle

    def new_job_dir(self) -> tuple[str, Path]:
        self._cleanup()
        job_id = uuid.uuid4().hex[:12]
        d = self.run_dir(job_id)
        d.mkdir(parents=True)
        return job_id, d

    def submit(self, job_id: str, video: Path, tap_log: Path) -> Future:
        job = {"job_id": job_id, "status": "queued", "stage": None, "error": None,
               "created_at": time.time(), "updated_at": time.time(), "result": None}
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        fut = self._pool.submit(self._run, job_id, video, tap_log)
        self._futures[job_id] = fut
        return fut

    def _run(self, job_id: str, video: Path, tap_log: Path) -> None:
        self._update(job_id, status="running")
        try:
            result = run_pipeline(video, tap_log, self.run_dir(job_id),
                                  progress=lambda stage: self._update(job_id, stage=stage))
            (self.run_dir(job_id) / "result.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            self._update(job_id, status="succeeded", stage="done", result=result)
        except PipelineError as exc:
            log.error("job %s failed: %s", job_id, exc)
            self._update(job_id, status="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - never leave a job stuck in "running"
            log.error("job %s crashed:\n%s", job_id, traceback.format_exc())
            self._update(job_id, status="failed", error=f"internal error: {exc!r}")

    def wait(self, job_id: str) -> dict[str, Any] | None:
        fut = self._futures.get(job_id)
        if fut:
            fut.result()
        return self.get(job_id)

    def _cleanup(self) -> None:
        cutoff = time.time() - settings.job_ttl_hours * 3600
        for d in self.runs_dir.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                with self._lock:
                    running = self._jobs.get(d.name, {}).get("status") in ("queued", "running")
                    if not running:
                        self._jobs.pop(d.name, None)
                        self._futures.pop(d.name, None)
                if not running:
                    shutil.rmtree(d, ignore_errors=True)


jobs = JobManager()
