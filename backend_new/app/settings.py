"""Service settings, read from environment variables (and backend_new/.env when present)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


_load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("API_KEY", "")
    seg_model: str = os.environ.get("SEG_MODEL", "google/gemini-2.5-flash-lite")
    # Frames segmented at once (each is one OpenRouter call).
    seg_concurrency: int = _int("SEG_CONCURRENCY", 8)
    # 0 = send full resolution. Downscaling to 1280 px cut recall to ~0.75 in testing.
    seg_max_side: int = _int("SEG_MAX_SIDE", 0)
    seg_jpeg_quality: int = _int("SEG_JPEG_QUALITY", 85)
    # ~25% of calls came back cut off (finish_reason=error) in testing, so retry generously.
    seg_retries: int = _int("SEG_RETRIES", 4)
    runs_dir: Path = Path(os.environ.get("RUNS_DIR", str(ROOT / "runs")))
    calibration_dir: Path = Path(os.environ.get(
        "CALIBRATION_DIR", str(ROOT / "pipeline_stages" / "tap_extractor" / "calibration")))
    # Pipeline jobs running at once; stage 1 decodes the whole video, so keep this low on small instances.
    max_concurrent_jobs: int = _int("MAX_CONCURRENT_JOBS", 1)
    max_upload_mb: int = _int("MAX_UPLOAD_MB", 300)
    job_ttl_hours: int = _int("JOB_TTL_HOURS", 24)
    # When set, every endpoint except /health requires header "X-API-Key: <value>".
    service_api_key: str = os.environ.get("SERVICE_API_KEY", "")


settings = Settings()
