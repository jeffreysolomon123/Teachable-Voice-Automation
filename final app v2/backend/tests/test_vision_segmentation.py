from __future__ import annotations

import pytest
from app.services.segmentation.base import RawSegmentation, SegmentationError
from app.services.segmentation.gemini_vision import GeminiVisionProvider, _parse_json_elements
from app.services.segmentation.groq_vision import GroqVisionProvider, _parse_groq_elements
from app.services.segmentation.pool import PoolProvider


def test_parse_gemini_json_elements():
    raw = """```json
    [
        {"id": 0, "type": "button", "text": "Order Now", "bbox": [100, 200, 300, 400]},
        {"id": 1, "type": "input", "text": "Search...", "bbox": [50, 50, 90, 950]}
    ]
    ```"""
    els = _parse_json_elements(raw)
    assert len(els) == 2
    assert els[0]["type"] == "button"
    assert els[0]["text"] == "Order Now"
    assert els[1]["bbox"] == [50, 50, 90, 950]


def test_parse_groq_json_elements_with_think_tags():
    raw = """<think>Thinking about layout...</think>
    [
        {"id": 0, "type": "text", "text": "Domino's", "bbox": [200, 100, 250, 300]}
    ]"""
    els = _parse_groq_elements(raw)
    assert len(els) == 1
    assert els[0]["text"] == "Domino's"


class DummySuccessProvider:
    name = "dummy_success"

    def status(self) -> str:
        return "configured"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        return RawSegmentation(width, height, [{"id": 0, "type": "button", "text": "OK", "bbox": [0, 0, 10, 10]}])


class DummyFailProvider:
    name = "dummy_fail"

    def status(self) -> str:
        return "configured"

    def segment(self, image_bytes: bytes, width: int, height: int) -> RawSegmentation:
        raise SegmentationError("Rate limit 429 quota exhausted")


def test_pool_provider_failover():
    fail_p = DummyFailProvider()
    succ_p = DummySuccessProvider()
    pool = PoolProvider([fail_p, succ_p])

    res = pool.segment(b"fake_jpeg", 100, 100)
    assert res.width == 100
    assert len(res.combined) == 1
    assert res.combined[0]["text"] == "OK"


def test_pool_provider_all_fail():
    p1 = DummyFailProvider()
    p2 = DummyFailProvider()
    pool = PoolProvider([p1, p2])

    with pytest.raises(SegmentationError) as exc_info:
        pool.segment(b"fake_jpeg", 100, 100)
    assert "All providers in pool failed" in str(exc_info.value)
