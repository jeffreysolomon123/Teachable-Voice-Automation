"""Typed configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    # CORS: comma-separated origins. Empty in production = no browser origins allowed
    # (the Android client is not a browser and is unaffected by CORS).
    cors_origins: str = ""

    # --- LLM (Groq, OpenAI-compatible API). Tiers that need a missing model are skipped.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    text_model: str = ""
    vision_model: str = ""
    # Sent as "reasoning_effort" when set (e.g. "low" for gpt-oss, "none" for qwen3 to skip thinking).
    text_reasoning_effort: str = ""
    vision_reasoning_effort: str = ""
    llm_timeout_seconds: float = 20.0
    # The vision model gets the screenshot scaled to this width (smaller = faster, cheaper).
    vision_image_width: int = 720
    # Most element boxes listed to the vision model (set-of-marks); keeps the prompt within TPM limits.
    vision_max_marks: int = 60

    # --- Segmentation. The provider is pluggable: "hf_space" (Gradio Space on Hugging Face),
    # "local" (load OmniParser + EasyOCR in this process) or "http" (any endpoint that accepts
    # an image and returns the same combined-elements JSON).
    segmentation_provider: Literal["hf_space", "local", "http"] = "hf_space"
    segmentation_timeout_seconds: float = 30.0
    hf_space: str = ""
    hf_token: str = ""
    segmentation_http_url: str = ""
    segmentation_http_token: str = ""
    # Folder holding segment_ui.py / ocr_text.py / combine_results.py (local provider only).
    local_segmentation_dir: str = "../tap-to-flow-pipeline/3_ui_segmentation"
    local_segmentation_device: str = ""
    seg_conf: float = 0.05
    seg_iou: float = 0.1
    seg_ocr_min_conf: float = 0.3
    seg_contain: float = 0.6
    # Screenshots are re-encoded as JPEG at this quality before upload to a remote provider.
    seg_upload_jpeg_quality: int = 90

    # --- Uploads
    max_image_bytes: int = 8 * 1024 * 1024
    max_image_pixels: int = 12_000_000
    allowed_image_types: str = "image/png,image/jpeg,image/webp"

    # --- Matching thresholds
    match_confident_threshold: float = Field(0.80, ge=0, le=1)
    match_ambiguity_margin: float = Field(0.08, ge=0, le=1)
    match_min_candidate_score: float = Field(0.45, ge=0, le=1)
    llm_confidence_threshold: float = Field(0.70, ge=0, le=1)
    vision_confidence_threshold: float = Field(0.70, ge=0, le=1)
    llm_max_candidates: int = 25

    # --- Replay
    max_retries: int = Field(3, ge=0, le=10)
    max_scroll_attempts: int = Field(3, ge=0, le=20)
    max_blocker_dismissals: int = Field(3, ge=0, le=20)
    screen_settle_delay_ms: int = 1000
    launch_settle_delay_ms: int = 3000
    session_ttl_seconds: int = 1800
    confirmation_ttl_seconds: int = 300
    max_sessions: int = 200
    max_slot_value_length: int = 200
    slot_min_confidence: float = Field(0.6, ge=0, le=1)

    # --- Storage
    flows_dir: str = "flows"

    # --- Voice assistant (assistant/voice_assistant_app) hosted in this service at /api/voice
    voice_assistant_enabled: bool = True
    voice_assistant_dir: str = "../assistant"

    # Known Android packages for LAUNCH_APP. JSON object in the env var, e.g.
    # APP_PACKAGES='{"Zomato": "com.application.zomato"}'. Flows may also set "package".
    app_packages: dict[str, str] = {
        "Zomato": "com.application.zomato",
        "Swiggy": "in.swiggy.android",
        "Flipkart": "com.flipkart.android",
    }

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_image_type_set(self) -> set[str]:
        return {t.strip().lower() for t in self.allowed_image_types.split(",") if t.strip()}

    @property
    def text_llm_enabled(self) -> bool:
        return bool(self.groq_api_key and self.text_model)

    @property
    def vision_llm_enabled(self) -> bool:
        return bool(self.groq_api_key and self.vision_model)

    def package_for(self, app: str | None) -> str | None:
        if not app:
            return None
        for name, pkg in self.app_packages.items():
            if name.casefold() == app.casefold():
                return pkg
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
