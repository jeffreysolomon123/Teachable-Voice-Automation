"""Semantic flows stored as one JSON file per flow (<flows_dir>/<flow_id>.json).

On Render the filesystem is ephemeral unless a persistent disk is mounted at FLOWS_DIR; flows
committed to backend/flows/ are always available after a deploy.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from ..models.flow import FLOW_ID_RE, Flow

_lock = threading.Lock()


class FlowStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _path(self, flow_id: str) -> Path:
        # flow_id is validated by Flow.flow_id's pattern ([a-z0-9_-]), so no path traversal.
        return self.dir / f"{flow_id}.json"

    def list(self) -> list[Flow]:
        if not self.dir.is_dir():
            return []
        flows = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                flows.append(Flow.model_validate_json(p.read_text(encoding="utf-8")))
            except (OSError, ValidationError, ValueError):
                continue  # a broken file must not take down the listing
        return flows

    def get(self, flow_id: str) -> Flow | None:
        if not re.fullmatch(FLOW_ID_RE, flow_id or ""):
            return None  # also rules out path traversal from the URL parameter
        p = self._path(flow_id)
        if not p.is_file():
            return None
        try:
            return Flow.model_validate_json(p.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def exists(self, flow_id: str) -> bool:
        return self._path(flow_id).is_file()

    def save(self, flow: Flow) -> None:
        with _lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(flow.model_dump(mode="json", exclude_none=True), f, indent=2, ensure_ascii=False)
                os.replace(tmp, self._path(flow.flow_id))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
