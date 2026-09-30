import asyncio

import httpx
import pytest

from app.config import Settings
from app.models.flow import FlowStep, Target
from app.models.segment import Element, Screen
from app.services.images import Screenshot
from app.services.llm_service import LLMService, parse_json_reply
from app.services.resolver import Resolver, Tier
from app.services.safety_service import detect_blocker, final_action_phrase, gate_reason
from app.services.segmentation import normalize
from app.services.segmentation.base import RawSegmentation, SegmentationError

from .conftest import FakeLLM, H, W, el, png_bytes


def E(i, text, bbox, type_="text"):
    return Element(index=i, text=text, bbox=bbox, type=type_)


AMBIG = Screen(width=W, height=H, elements=[
    E(0, "Domino's Pizza", (0, 300, 500, 360)), E(1, "Domino's Express", (0, 900, 500, 960)),
    E(2, "Pizza Hut", (0, 1500, 500, 1560))])
TARGET = Target(description="restaurant matching Domino's", slot="restaurant")
SLOTS = {"restaurant": "Domino's"}


def resolver(fake: FakeLLM, **kw) -> Resolver:
    s = Settings(_env_file=None, groq_api_key="k", text_model="t", vision_model="v", **kw)
    return Resolver(s, LLMService(s, transport=httpx.MockTransport(fake.handler)))


def shot() -> Screenshot:
    return Screenshot(png_bytes(), W, H, "PNG")


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- LLM tiers

def test_parse_json_reply_strips_think_and_fences():
    assert parse_json_reply('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_deterministic_hit_makes_no_llm_call():
    fake = FakeLLM()
    screen = Screen(width=W, height=H, elements=[E(0, "Pizza Hut", (0, 300, 500, 360)), E(1, "Subway", (0, 900, 500, 960))])
    res = run(resolver(fake).resolve(Target(description="x", text="Pizza Hut"), screen, {}, shot()))
    assert res.result.found and res.result.method == "deterministic" and fake.requests == []


def test_text_llm_disambiguates_and_backend_computes_coordinates():
    fake = FakeLLM()
    fake.push('{"selected_index": 1, "confidence": 0.91}')
    res = run(resolver(fake).resolve(TARGET, AMBIG, SLOTS, shot()))
    r = res.result
    assert r.found and r.method == "text_llm" and r.index == 1 and (r.x, r.y) == (250, 930)
    prompt = fake.requests[0]["messages"][0]["content"]
    assert "0: Domino's Pizza" in prompt and "1: Domino's Express" in prompt and "x=" not in prompt


@pytest.mark.parametrize("reply", [
    "not json at all",
    '{"selected_index": "one", "confidence": 0.9}',
    '{"selected_index": 1, "confidence": 3}',
    '{"selected_index": 7, "confidence": 0.99}',     # not a candidate
    '{"selected_index": 1, "confidence": 0.2}',      # below threshold
    500,                                             # HTTP error
])
def test_bad_text_llm_output_never_becomes_an_action(reply):
    fake = FakeLLM()
    fake.push(reply, '{"found": false, "confidence": 0}')
    r = run(resolver(fake).resolve(TARGET, AMBIG, SLOTS, shot())).result
    assert not r.found


def test_vision_fallback_scales_and_validates_coordinates():
    fake = FakeLLM()
    # Vision gets a 720px wide image (scale 720/1080); (360, 700) maps back to (540, 1050).
    fake.push('{"selected_index": null, "confidence": 0.1}', '{"found": true, "x": 360, "y": 700, "confidence": 0.9}')
    r = run(resolver(fake).resolve(TARGET, AMBIG, SLOTS, shot())).result
    assert r.found and r.method == "vision_llm" and (r.x, r.y) == (540, 1050)
    content = fake.requests[1]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "720x1600" in content[0]["text"]


def test_vision_set_of_marks_uses_the_chosen_box():
    fake = FakeLLM()
    fake.push('{"selected_index": null, "confidence": 0}', '{"found": true, "index": 2, "confidence": 0.9}')
    r = run(resolver(fake).resolve(TARGET, AMBIG, SLOTS, shot())).result
    assert r.found and r.method == "vision_llm" and r.index == 2 and (r.x, r.y) == (250, 1530)
    text = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "2: [0,1000,333,1040]" in text  # boxes are given in the scaled image's pixels


@pytest.mark.parametrize("reply", ['{"found": true, "index": 99, "confidence": 0.95}',
                                   '{"found": true, "x": 5000, "y": 10, "confidence": 0.95}',
                                   '{"found": true, "confidence": 0.95}',
                                   '{"found": true, "x": 100, "y": 100, "confidence": 0.3}'])
def test_invalid_vision_output_rejected(reply):
    fake = FakeLLM()
    fake.push('{"selected_index": null, "confidence": 0}', reply)
    assert not run(resolver(fake).resolve(TARGET, AMBIG, SLOTS, shot())).result.found


def test_min_tier_forces_llm_even_when_deterministic_found():
    fake = FakeLLM()
    fake.push('{"selected_index": 0, "confidence": 0.95}')
    screen = Screen(width=W, height=H, elements=[E(0, "Pizza Hut", (0, 300, 500, 360))])
    r = run(resolver(fake).resolve(Target(description="x", text="Pizza Hut"), screen, {}, shot(),
                                   min_tier=Tier.TEXT_LLM)).result
    assert r.method == "text_llm" and len(fake.requests) == 1


def test_llm_disabled_without_key():
    s = Settings(_env_file=None, groq_api_key="", text_model="t")
    fake = FakeLLM()
    r = run(Resolver(s, LLMService(s, transport=httpx.MockTransport(fake.handler))).resolve(TARGET, AMBIG, SLOTS)).result
    assert not r.found and fake.requests == []


# ----------------------------------------------------------------------------- safety

@pytest.mark.parametrize("text,phrase", [("Place Order", "place order"), ("Pay ₹598", "pay"), ("PAY NOW", "pay now"),
                                         ("Swipe to pay", "swipe to pay"), ("Confirm payment", "confirm payment"),
                                         ("Proceed to Pay", "proceed to pay")])
def test_final_action_phrases(text, phrase):
    assert final_action_phrase(text) is not None


@pytest.mark.parametrize("text", ["Paytm wallet", "Payment methods", "Add item", "View cart", "Pizza", "Replay"])
def test_non_final_labels(text):
    assert final_action_phrase(text) is None


def test_gate_reason_checks_element_role_and_flag():
    screen = Screen(width=W, height=H, elements=[])
    tap = FlowStep(step=1, action="TAP", target=Target(description="checkout button"))
    assert gate_reason(tap, E(0, "Place order ₹357", (0, 2200, 1000, 2300)), screen)
    assert gate_reason(tap, E(0, "Add item", (0, 2200, 1000, 2300)), screen) is None
    role = FlowStep(step=1, action="TAP", target=Target(description="x", semantic_role="place_order"))
    assert gate_reason(role, None, screen)
    flagged = FlowStep(step=1, action="TAP", target=Target(description="x"), final_confirmation=True)
    assert gate_reason(flagged, E(0, "Next", (0, 0, 10, 10)), screen)


def S(*texts):
    return Screen(width=W, height=H, elements=[E(i, t, (40, 200 + 100 * i, 900, 280 + 100 * i)) for i, t in enumerate(texts)])


def test_blocker_detection():
    b = detect_blocker(S("Allow Zomato to send you notifications?", "Allow", "Don't allow"), strong_only=True)
    assert b.kind == "permission_dialog" and not b.needs_user and b.dismiss.text == "Don't allow"
    b = detect_blocker(S("Allow Zomato to access this device's location?", "While using the app", "Only this time",
                         "Don't allow"), strong_only=True)
    assert b.kind == "location_permission" and b.needs_user
    b = detect_blocker(S("Enter your mobile number", "Continue"), strong_only=True)
    assert b.kind == "login" and b.needs_user
    assert detect_blocker(S("Enter UPI PIN"), strong_only=True).kind == "payment_screen"
    assert detect_blocker(S("Something went wrong", "Try again"), strong_only=False).dismiss.text == "Try again"
    assert detect_blocker(S("No internet connection"), strong_only=False).needs_user
    assert detect_blocker(S("Domino's", "Currently unavailable"), strong_only=False).kind == "unavailable"
    popup = detect_blocker(S("Get 50% off on your first order!", "Maybe later"), strong_only=False)
    assert popup.kind == "popup" and popup.dismiss.text == "Maybe later"


def test_no_false_blockers_on_normal_screens():
    normal = S("Home", "Search for restaurants", "Sign in to see offers", "Domino's Pizza", "Recommended")
    assert detect_blocker(normal, strong_only=True) is None
    assert detect_blocker(normal, strong_only=False) is None
    assert detect_blocker(S("OK", "Clear cart?"), strong_only=False) is None  # never auto-press OK


# ----------------------------------------------------------------------------- segmentation / images

def test_normalize_reindexes_in_reading_order_and_remaps_parent():
    raw = RawSegmentation(W, H, [
        {"id": 0, "text": "Card", "bbox": [0, 500, 1000, 900], "type": "container", "source": "cv", "parent": None},
        {"id": 1, "text": "Top", "bbox": [0, 100, 500, 150], "type": "text", "source": "ocr", "parent": None},
        {"id": 2, "text": "Inner", "bbox": [10, 600, 500, 650], "type": "labeled_element", "source": "cv", "parent": 0},
        {"id": 3, "text": "bad", "bbox": [5, 5, 5, 5], "type": "text", "source": "ocr"},
        {"id": 4, "text": "  two   spaces ", "bbox": [0, 2390, 2000, 2600], "type": "text", "source": "ocr"},
    ])
    els = normalize(raw)
    assert [e.text for e in els] == ["Top", "Card", "Inner", "two spaces"]
    assert [e.index for e in els] == [0, 1, 2, 3] and els[2].parent == 1
    assert els[3].bbox == (0, 2390, 1080, 2400)  # clamped to the screenshot


def test_segment_endpoint(client, provider):
    provider.push([el("Search for restaurants", (40, 100, 1030, 180)), el("Domino's", (80, 450, 500, 530))])
    r = client.post("/segment", files={"file": ("s.png", png_bytes(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["width"] == W and body["height"] == H and body["provider"] == "fake"
    assert body["elements"][1] == {"index": 1, "text": "Domino's", "label": "", "interactive": False,
                                   "type": "text", "bbox": [80, 450, 500, 530], "confidence": 0.9,
                                   "source": "ocr", "parent": None, "center": [290, 490]}


def test_segment_rejects_bad_uploads(make_client, provider):
    c = make_client(max_image_bytes=50_000)
    r = c.post("/segment", files={"file": ("s.txt", b"hello", "text/plain")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "SCREEN_INVALID"
    r = c.post("/segment", files={"file": ("s.png", b"not an image", "image/png")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "SCREEN_INVALID"
    r = c.post("/segment", files={"file": ("s.png", png_bytes()[:200], "image/png")})
    assert r.json()["error"]["code"] == "SCREEN_INVALID"
    big = b"\x89PNG" + b"0" * 60_000
    assert c.post("/segment", files={"file": ("s.png", big, "image/png")}).status_code == 413
    assert provider.calls == 0


def test_segmentation_failure_is_reported(client, provider):
    provider.push(SegmentationError("Space GPU quota exhausted"))
    r = client.post("/segment", files={"file": ("s.jpg", png_bytes(fmt="JPEG"), "image/jpeg")})
    assert r.status_code == 502
    assert r.json()["error"] == {"code": "SEGMENTATION_FAILED", "message": "Space GPU quota exhausted", "retryable": True}


def test_health_never_exposes_secrets(make_client):
    c = make_client(llm=True, groq_api_key="gsk_secret_value", hf_token="hf_secret_value")
    assert c.get("/health").json() == {"status": "ok"}
    r = c.get("/health/dependencies")
    deps = r.json()
    assert deps["api"] == "ok" and deps["segmentation"].startswith("fake") and deps["llm"] == "configured"
    assert "secret_value" not in r.text
