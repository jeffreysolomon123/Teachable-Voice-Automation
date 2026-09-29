"""Wires services together once per app instance (tests build their own with fakes)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from .config import Settings
from .services.llm_service import LLMService
from .services.replay_service import ReplayService, SessionStore
from .services.resolver import Resolver
from .services.segmentation import SegmentationService, build_provider
from .services.segmentation.base import SegmentationProvider
from .storage.flow_store import FlowStore


@dataclass
class Container:
    settings: Settings
    flows: FlowStore
    segmentation: SegmentationService
    llm: LLMService
    resolver: Resolver
    sessions: SessionStore
    replay: ReplayService
    # Set when the voice assistant is mounted; stored flows are registered with it.
    voice: Optional[object] = None

    def flow_saved(self, flow) -> None:
        if self.voice is not None:
            try:
                self.voice.register_flow(flow)
            except Exception:  # the assistant's memory is best-effort; never fail a flow save
                pass


def build_container(settings: Settings, provider: Optional[SegmentationProvider] = None,
                    llm: Optional[LLMService] = None) -> Container:
    flows = FlowStore(settings.flows_dir)
    seg = SegmentationService(provider or build_provider(settings), settings)
    llm = llm or LLMService(settings)
    sessions = SessionStore(settings)
    return Container(settings, flows, seg, llm, Resolver(settings, llm), sessions,
                     ReplayService(settings, flows, sessions, seg, llm))


def get_container(request: Request) -> Container:
    return request.app.state.container
