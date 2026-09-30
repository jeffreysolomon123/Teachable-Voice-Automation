"""FastAPI entry point: uvicorn app.main:app --host 0.0.0.0 --port $PORT"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# backend/.env -> os.environ, so the mounted voice assistant (which reads os.environ) also gets
# OPENROUTER_API_KEY. Real environment variables win.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.teach_routes import router as teach_router
from .config import Settings, get_settings
from .container import build_container
from .errors import install_error_handlers
from .services.llm_service import LLMService
from .services.segmentation.base import SegmentationProvider
from .voice_integration import mount_voice_assistant


def create_app(settings: Optional[Settings] = None, *, provider: Optional[SegmentationProvider] = None,
               llm: Optional[LLMService] = None, voice: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Visual UI Agent Backend", version="1.0.0")
    app.state.container = build_container(settings, provider, llm)

    origins = settings.cors_origin_list
    if not origins and settings.app_env == "development":
        origins = ["http://localhost", "http://localhost:3000", "http://127.0.0.1", "http://127.0.0.1:3000"]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["*"], allow_credentials=False)

    install_error_handlers(app)
    app.include_router(router)
    app.include_router(teach_router)
    if voice:
        container = app.state.container
        container.voice = mount_voice_assistant(app, settings, container.flows)
        if container.voice is not None:
            container.replay.reporter = container.voice.record_run
    return app


app = create_app()
