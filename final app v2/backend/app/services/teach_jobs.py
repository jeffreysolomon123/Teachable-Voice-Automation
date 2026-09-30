"""Background TEACH jobs: recording + tap log + plan -> grounded_flow.json -> saved semantic flow.

Each job lives in TEACH_RUNS_DIR/<job_id>/ (inputs, pipeline outputs, grounded_flow.json,
result.json, job.json). Jobs run one at a time: stage 1 decodes the whole video.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Settings
from ..models.flow import Flow
from ..models.teach import TeachRequest
from ..storage.flow_store import FlowStore
from .llm_service import LLMService
from .teach_pipeline import PipelineError, run_pipeline
from .teach_service import teach

log = logging.getLogger("app.teach.jobs")


def flow_id_for(plan: dict[str, Any]) -> str:
    """The plan's flow_id if valid, else a slug of app + summary (FLOW_ID_RE: ^[a-z0-9][a-z0-9_-]{0,63}$)."""
    fid = str(plan.get("flow_id") or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,63}", fid):
        return fid
    raw = f"{plan.get('app_name') or ''} {plan.get('summary') or 'taught flow'}".lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")[:56] or "taught_flow"
    return slug if slug[0].isalnum() else f"f{slug}"


def add_labels(grounded: list[dict[str, Any]], run_dir: Path) -> None:
    """Copy the LLM segmentation's ``label`` (e.g. "back arrow") of each grounded element from its
    before-frame's _combined.json. Stage 4 only copies ``text``, so text-less icons/images would
    otherwise reach TEACH with no description at all."""
    cache: dict[Path, dict[Any, dict[str, Any]]] = {}
    for st in grounded:
        g, frame = st.get("grounded_element"), st.get("before_frame")
        if not isinstance(g, dict) or not frame:
            continue
        p = Path(frame)
        combined = (p if p.is_absolute() else run_dir / p).parent / "segment_out" / f"{p.stem}_combined.json"
        if combined not in cache:
            try:
                data = json.loads(combined.read_text(encoding="utf-8"))
                cache[combined] = {e["id"]: e for e in data.get("elements", [])}
            except (OSError, ValueError, KeyError):
                cache[combined] = {}
        el = cache[combined].get(g.get("id")) or {}
        g["label"] = el.get("label") or ""
        g["interactive"] = bool(el.get("interactive", False))


def task_steps(grounded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop taps on the recorder's own Stop control (they are not part of the task)."""
    def is_stop(st: dict[str, Any]) -> bool:
        el = st.get("element") or {}
        return st.get("type") == "tap" and el.get("className") == "StopButton"
    return [st for st in grounded if not is_stop(st)]


class TeachJobs:
    def __init__(self, settings: Settings, flows: FlowStore, llm: LLMService,
                 on_flow_saved: Callable[[Flow], None]):
        self.s, self.flows, self.llm, self.on_flow_saved = settings, flows, llm, on_flow_saved
        self.runs_dir = Path(settings.teach_runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="teach")
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def run_dir(self, job_id: str) -> Path:
        return self.runs_dir / job_id

    def new_job(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex[:12]
        d = self.run_dir(job_id)
        (d / "input").mkdir(parents=True)
        return job_id, d

    def _save(self, job: dict[str, Any]) -> None:
        path = self.run_dir(job["job_id"]) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields, updated_at=time.time())
            self._save(job)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            if job_id in self._jobs:
                return dict(self._jobs[job_id])
        path = self.run_dir(job_id) / "job.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def submit(self, job_id: str, video: Path, tap_log: Path, plan: dict[str, Any]) -> None:
        job = {"job_id": job_id, "status": "queued", "stage": None, "error": None,
               "flow_id": flow_id_for(plan), "app": plan.get("app_name"), "created_at": time.time(),
               "updated_at": time.time(), "result": None}
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        self._pool.submit(self._run, job_id, video, tap_log, plan)

    # ------------------------------------------------------------------ the job

    def _run(self, job_id: str, video: Path, tap_log: Path, plan: dict[str, Any]) -> None:
        self._update(job_id, status="running")
        run_dir = self.run_dir(job_id)
        try:
            out = run_pipeline(video, tap_log, run_dir, self.s, progress=lambda st: self._update(job_id, stage=st))
            add_labels(out["grounded_flow"], run_dir)
            self._update(job_id, stage="building_flow")
            flow, warnings, llm_used = self._build_flow(plan, out["grounded_flow"])
            result = {"flow": flow.model_dump(mode="json"), "grounded_flow": out["grounded_flow"],
                      "summary": out["summary"], "teach_warnings": warnings, "llm_used": llm_used}
            (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            self._update(job_id, status="succeeded", stage="done", result=result)
            log.info("teach job %s -> flow %s (%d steps)", job_id, flow.flow_id, len(flow.steps))
        except PipelineError as exc:
            log.error("teach job %s failed: %s", job_id, exc)
            self._update(job_id, status="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - never leave a job stuck in "running"
            log.error("teach job %s crashed:\n%s", job_id, traceback.format_exc())
            self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")

    def _build_flow(self, plan: dict[str, Any], grounded: list[dict[str, Any]]) -> tuple[Flow, list[str], bool]:
        """grounded_flow -> semantic Flow (existing teach()), saved to the flow store."""
        steps = task_steps(grounded)
        if not any(st.get("type") in ("tap", "type_text") for st in steps):
            raise PipelineError("no taps were found in the recording (was 'Show taps' on?)")
        app = str(plan.get("app_name") or "").strip() or "App"
        slots = {k: v for k, v in (plan.get("slots") or {}).items()
                 if k != "app" and v not in (None, "") and isinstance(v, (str, int, float, bool))}
        req = TeachRequest(
            flow_id=flow_id_for(plan), app=app, package=self.s.package_for(app) or plan.get("package"),
            description=str(plan.get("summary") or f"Taught flow on {app}")[:500], slots=slots,
            grounded_steps=steps, use_llm=True, save=True, overwrite=True)
        res = asyncio.run(teach(req, self.llm))
        self.flows.save(res.flow)
        self.on_flow_saved(res.flow)
        return res.flow, res.warnings, res.llm_used
