"""Provider: Multi-provider load-balanced pool for segmentation.

Pools multiple providers (e.g., Groq Vision + Gemini Flash) to deliver:
- High combined throughput (30 RPM Groq + 15 RPM Gemini = 45 RPM).
- Instant, transparent failover if any provider hits rate limits (429) or transient errors.
- Zero-downtime reliability with zero notebook or GPU dependencies.
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

from .base import RawSegmentation, SegmentationError, SegmentationProvider

log = logging.getLogger("app.segmentation.pool")


class PoolProvider:
    name = "pool"

    def __init__(self, providers: Sequence[SegmentationProvider]):
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("PoolProvider requires at least one provider")
        self._provider_cooldowns: dict[str, float] = {}

    def status(self) -> str:
        parts = [f"{p.name}({p.status()})" for p in self.providers]
        return f"pool[{', '.join(parts)}]"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        now = time.time()
        last_error = None

        # Prioritize providers that are not currently cooling down from a 429
        available = [p for p in self.providers if self._provider_cooldowns.get(p.name, 0) <= now]
        candidates = available if available else self.providers

        for provider in candidates:
            try:
                result = provider.segment(image_bytes, width, height)
                self._provider_cooldowns.pop(provider.name, None)
                return result
            except SegmentationError as exc:
                msg = str(exc).lower()
                last_error = exc
                if "429" in msg or "quota" in msg or "rate limit" in msg or "resource_exhausted" in msg:
                    log.warning("Provider %s hit rate limit, cooling down for 15s; failing over: %s", provider.name, exc)
                    self._provider_cooldowns[provider.name] = now + 15.0
                else:
                    log.warning("Provider %s failed (%s); failing over to next provider", provider.name, exc)

        raise SegmentationError(f"All providers in pool failed. Last error: {last_error}")
