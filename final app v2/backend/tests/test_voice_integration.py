"""The voice assistant (assistant/voice_assistant_app) hosted inside this backend."""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_integration import VoiceBridge

from .conftest import FakeProvider, upload
from .test_replay import FLOW, HOME, MENU, RESULTS, SEARCH


class FakeMemory:
    def __init__(self):
        self.workflows, self.runs = {}, []

    def get_all_workflows(self):
        return self.workflows

    def save_workflow(self, flow_id, app_name, trigger_phrases, default_slots, description=""):
        self.workflows[flow_id] = {"app_name": app_name, "trigger_phrases": trigger_phrases}

    def record_run_result(self, flow_id, status, step_stopped, details=""):
        self.runs.append((flow_id, status, step_stopped))


@pytest.fixture(scope="module")
def voice_client(tmp_path_factory):
    data = Path(os.environ["VOICE_DATA_DIR"])  # set in conftest before the assistant is imported
    flows = tmp_path_factory.mktemp("flows")
    src = Path(__file__).resolve().parents[1] / "flows" / "order_food_zomato.json"
    (flows / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    assistant_dir = Path(__file__).resolve().parents[2] / "assistant"
    s = Settings(_env_file=None, app_env="test", flows_dir=str(flows), voice_assistant_dir=str(assistant_dir))
    app = create_app(s, provider=FakeProvider())
    if app.state.container.voice is None:
        pytest.skip("voice assistant dependencies not installed")
    return TestClient(app), data


def test_voice_routes_and_mobile_ui_are_served(voice_client):
    c, _ = voice_client
    assert c.get("/api/voice/health").json()["status"] == "ok"
    r = c.get("/mobile/")
    assert r.status_code == 200 and "app.js" in r.text
    js = c.get("/mobile/app.js").text
    assert "AndroidBridge.startReplay" in js and "onReplayEvent" in js
    assert c.get("/health").json() == {"status": "ok"}  # our routes are unaffected
    assert c.get("/health/dependencies").json()["voice_assistant"] == "mounted"


def test_semantic_flows_are_registered_with_the_assistant(voice_client):
    c, data = voice_client
    wf = c.get("/api/voice/workflows").json()["workflows"]
    assert "order_food_zomato" in wf and wf["order_food_zomato"]["app_name"] == "Zomato"
    c.post("/flows", json={**FLOW, "flow_id": "voice_demo_flow"})
    assert "voice_demo_flow" in c.get("/api/voice/workflows").json()["workflows"]
    assert (data / "workflows.json").is_file()


def test_replay_outcomes_reach_the_assistant_memory(make_settings, provider):
    s = make_settings()
    app = create_app(s, provider=provider, voice=False)
    mem = FakeMemory()
    bridge = VoiceBridge(mem)
    app.state.container.voice = bridge
    app.state.container.replay.reporter = bridge.record_run
    c = TestClient(app)
    c.post("/flows", json=FLOW)
    assert "demo_order" in mem.workflows  # flow saved -> registered
    c.post("/replay/start", json={"flow_id": "demo_order", "slots": {"restaurant": "Domino's"}, "session_id": "v1"})
    for screen in (HOME, SEARCH, RESULTS, MENU):
        provider.push(screen)
        r = c.post("/replay/step", data={"session_id": "v1"}, files=upload())
    assert r.json()["action"]["kind"] == "payment_gate"
    assert mem.runs[-1][:2] == ("demo_order", "PAUSED_AT_PAYMENT")
    c.post("/replay/stop", json={"session_id": "v1"})
    assert mem.runs[-1][:2] == ("demo_order", "STOPPED")


def test_bridge_sync_only_adds_missing(flows_dir):
    from app.storage.flow_store import FlowStore
    mem = FakeMemory()
    mem.workflows["order_food_zomato"] = {"app_name": "Zomato"}
    assert VoiceBridge(mem).sync(FlowStore(flows_dir)) == 0
