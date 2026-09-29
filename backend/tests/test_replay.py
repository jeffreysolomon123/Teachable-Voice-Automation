import pytest

from app.models.replay import ReplayState as S
from app.services.replay_service import TRANSITIONS, InvalidTransition, ReplaySession

from .conftest import el, upload

FLOW = {
    "flow_id": "demo_order", "app": "Zomato", "description": "Order food from a restaurant on Zomato",
    "required_slots": ["restaurant"], "optional_slots": ["note"],
    "steps": [
        {"step": 1, "action": "LAUNCH_APP", "app": "Zomato"},
        {"step": 2, "action": "TAP", "target": {"description": "search field", "semantic_role": "search_field"}},
        {"step": 3, "action": "TYPE", "value": "{{restaurant}}"},
        {"step": 4, "action": "TAP", "target": {"description": "restaurant matching {{restaurant}}",
                                                "slot": "restaurant", "text": "{{restaurant}}"},
         "verify": {"mode": "anchors", "anchors": ["{{restaurant}}"]}},
        {"step": 5, "action": "TYPE", "value": "{{note}}"},
        {"step": 6, "action": "TAP", "target": {"description": "Place order button", "semantic_role": "place_order"}},
    ],
}

HOME = [el("Deliver to Home", (40, 100, 700, 160)), el('Search "biryani"', (40, 200, 1000, 280)),
        el("Recommended for you", (40, 700, 800, 760))]
SEARCH = [el("Search for restaurant or dish", (40, 150, 1000, 230)), el("Recent searches", (40, 400, 600, 460))]
RESULTS = [el("Domino's", (40, 150, 1000, 230)), el("Domino's Pizza", (40, 500, 600, 560)),
           el("Pizza Hut", (40, 700, 600, 760))]
MENU = [el("Domino's Pizza", (40, 150, 800, 230)), el("Margherita", (40, 600, 600, 660)),
        el("Place order ₹598", (40, 2200, 1040, 2320))]
DONE = [el("Order placed!", (40, 900, 1040, 1000))]


@pytest.fixture
def c(client):
    assert client.post("/flows", json=FLOW).status_code == 201
    return client


def start(c, **slots):
    return c.post("/replay/start", json={"flow_id": "demo_order", "slots": slots or {"restaurant": "Domino's"},
                                         "session_id": "sess_test"})


def step(c, provider, screen, result="ok"):
    provider.push(screen)
    return c.post("/replay/step", data={"session_id": "sess_test", "last_action_result": result}, files=upload())


def act(r):
    return r.json()["action"]


def test_happy_path_with_payment_gate(c, provider):
    r = start(c).json()
    assert r["status"] == "running" and r["state"] == "EXECUTING_ACTION"
    assert r["next_action"] == r["action"]
    assert r["action"]["action"] == "LAUNCH_APP" and r["action"]["package"] == "com.application.zomato"

    a = act(step(c, provider, HOME))
    assert a["action"] == "TAP" and (a["x"], a["y"]) == (520, 240) and a["method"] == "deterministic"
    assert a["screen_width"] == 1080 and a["screen_height"] == 2400

    a = act(step(c, provider, SEARCH))
    assert a == {**a, "action": "TYPE", "text": "Domino's"}

    r = step(c, provider, RESULTS).json()
    # The search box echoes "Domino's" exactly; the result card must be chosen instead.
    assert r["step"] == 4 and r["action"]["action"] == "TAP" and r["action"]["y"] == 530

    r = step(c, provider, MENU).json()  # anchors verified; step 5 skipped (note empty); step 6 gated
    assert r["state"] == "WAITING_FOR_CONFIRMATION" and r["status"] == "waiting_for_user" and r["step"] == 6
    ask = r["action"]
    assert ask["action"] == "ASK_USER" and ask["kind"] == "payment_gate" and "final order/payment" in ask["reason"]

    # More screenshots while waiting don't advance anything (and aren't even segmented).
    calls = provider.calls
    again = step(c, provider, MENU).json()
    assert again["action"]["confirmation_id"] == ask["confirmation_id"] and again["state"] == "WAITING_FOR_CONFIRMATION"
    assert provider.calls == calls
    provider.queue.clear()

    bad = c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True, "confirmation_id": "conf_x"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "CONFIRMATION_STALE"

    r = c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True,
                                        "confirmation_id": ask["confirmation_id"]}).json()
    assert r["state"] == "WAITING_FOR_SCREEN" and r["action"]["action"] == "WAIT"

    a = act(step(c, provider, MENU))
    assert a["action"] == "TAP" and a["y"] == 2260

    r = step(c, provider, DONE).json()
    assert r["status"] == "completed" and r["action"]["action"] == "STOP"
    # A confirmation after the fact is stale.
    assert c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True}).status_code == 409


def _to_gate(c, provider):
    start(c)
    for s in (HOME, SEARCH, RESULTS):
        step(c, provider, s)
    return step(c, provider, MENU).json()["action"]


def test_declining_the_gate_stops(c, provider):
    ask = _to_gate(c, provider)
    r = c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": False,
                                        "confirmation_id": ask["confirmation_id"]}).json()
    assert r["status"] == "stopped" and r["action"]["action"] == "STOP"


def test_approval_does_not_cover_a_different_payment_label(c, provider):
    ask = _to_gate(c, provider)
    c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True,
                                    "confirmation_id": ask["confirmation_id"]})
    changed = [el("Domino's Pizza", (40, 150, 800, 230)), el("Pay ₹1,250", (40, 2200, 1040, 2320))]
    r = step(c, provider, changed).json()
    assert r["action"]["action"] == "ASK_USER" and r["action"]["confirmation_id"] != ask["confirmation_id"]


def test_gate_cannot_be_skipped_without_confirm(c, provider):
    _to_gate(c, provider)
    for _ in range(3):
        assert act(step(c, provider, MENU))["action"] == "ASK_USER"


def test_retry_ladder_then_ask_user(c, provider):
    start(c)
    assert act(step(c, provider, HOME))["action"] == "TAP"
    # Screen doesn't change after the tap: recapture, then re-resolve (attempts 2 and 3), then ask.
    assert act(step(c, provider, HOME))["action"] == "WAIT"
    assert act(step(c, provider, HOME))["action"] == "TAP"
    assert act(step(c, provider, HOME))["action"] == "TAP"
    r = step(c, provider, HOME).json()
    assert r["action"]["action"] == "ASK_USER" and r["action"]["kind"] == "retries_exhausted"
    assert r["state"] == "ASKING_USER"
    r = c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True,
                                        "confirmation_id": r["action"]["confirmation_id"]}).json()
    assert r["state"] == "WAITING_FOR_SCREEN"
    assert act(step(c, provider, SEARCH))["action"] == "TAP"  # re-resolves step 2 on the fresh screen


def test_max_retries_is_configurable(make_client, provider):
    c = make_client(max_retries=1)
    c.post("/flows", json=FLOW)
    start(c)
    step(c, provider, HOME)
    assert act(step(c, provider, HOME))["action"] == "WAIT"
    assert act(step(c, provider, HOME))["kind"] == "retries_exhausted"


def test_target_not_found_recaptures_then_asks(c, provider):
    start(c)
    empty = [el("Loading", (40, 1000, 600, 1060))]
    actions = [act(step(c, provider, empty))["action"] for _ in range(4)]
    assert actions == ["WAIT", "WAIT", "WAIT", "ASK_USER"]


def test_llm_tier_used_on_retry_when_configured(make_client, provider, fake_llm):
    c = make_client(llm=True)
    c.post("/flows", json=FLOW)
    start(c)
    screen = [el("Deliver to Home", (40, 100, 700, 160)), el("Find food", (40, 200, 1000, 280))]
    assert act(step(c, provider, screen))["action"] == "WAIT"  # nothing matches: recapture first, no LLM
    assert fake_llm.requests == []
    fake_llm.push('{"selected_index": 1, "confidence": 0.9}')
    a = act(step(c, provider, screen))
    assert a["action"] == "TAP" and a["method"] == "text_llm" and a["y"] == 240


def test_permission_dialog_is_dismissed_then_flow_continues(c, provider):
    start(c)
    dialog = [el("Allow Zomato to send you notifications?", (80, 900, 1000, 980)), el("Allow", (400, 1100, 700, 1160)),
              el("Don't allow", (400, 1200, 700, 1260))]
    a = act(step(c, provider, dialog))
    assert a["action"] == "TAP" and a["method"] == "blocker" and a["y"] == 1230
    assert act(step(c, provider, HOME))["action"] == "TAP"  # step 2 resolves after the dialog is gone


def test_login_wall_asks_user(c, provider):
    start(c)
    r = step(c, provider, [el("Enter your mobile number", (40, 800, 1000, 880)), el("Continue", (40, 1000, 1000, 1080))])
    body = r.json()
    assert body["action"]["action"] == "ASK_USER" and body["action"]["kind"] == "blocker" and "login" in body["message"]


def test_input_not_supported_falls_back_to_user(c, provider):
    start(c)
    step(c, provider, HOME)
    assert act(step(c, provider, SEARCH))["action"] == "TYPE"
    calls = provider.calls
    r = step(c, provider, SEARCH, result="input_not_supported").json()
    assert r["action"]["kind"] == "input_fallback" and "Domino's" in r["action"]["reason"]
    assert provider.calls == calls  # answered without segmenting
    provider.queue.clear()
    c.post("/replay/confirm", json={"session_id": "sess_test", "confirmed": True,
                                    "confirmation_id": r["action"]["confirmation_id"]})
    r = step(c, provider, RESULTS).json()
    assert r["step"] == 4 and r["action"]["action"] == "TAP"


def test_app_not_installed_fails(c, provider):
    start(c)
    r = step(c, provider, HOME, result="app_not_installed").json()
    assert r["status"] == "failed" and r["action"]["action"] == "STOP"


def test_scroll_search_swipes_when_target_absent(c, provider):
    flow = dict(FLOW, flow_id="scroll_flow", steps=[
        {"step": 1, "action": "TAP", "target": {"description": "x", "text": "Farmhouse", "scroll_search": True}}])
    c.post("/flows", json=flow)
    c.post("/replay/start", json={"flow_id": "scroll_flow", "slots": {"restaurant": "x"}, "session_id": "sess_test"})
    a = act(step(c, provider, MENU))
    assert a["action"] == "SWIPE" and a["y1"] > a["y2"]
    a = act(step(c, provider, MENU + [el("Farmhouse", (40, 1500, 600, 1560))]))
    assert a["action"] == "TAP" and a["y"] == 1530


def test_optional_step_is_skipped_when_absent(c, provider):
    flow = dict(FLOW, flow_id="opt_flow", steps=[
        {"step": 1, "action": "TAP", "optional": True, "target": {"description": "sheet", "text": "Add item"}},
        {"step": 2, "action": "TAP", "target": {"description": "menu item", "text": "Margherita"}}])
    c.post("/flows", json=flow)
    c.post("/replay/start", json={"flow_id": "opt_flow", "slots": {"restaurant": "x"}, "session_id": "sess_test"})
    assert act(step(c, provider, MENU))["action"] == "WAIT"
    a = act(step(c, provider, MENU))
    assert a["action"] == "TAP" and a["y"] == 630


def test_start_validation(c):
    r = c.post("/replay/start", json={"flow_id": "demo_order", "slots": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "MISSING_SLOT"
    r = c.post("/replay/start", json={"flow_id": "nope", "slots": {"restaurant": "x"}})
    assert r.status_code == 404 and r.json()["error"]["code"] == "FLOW_NOT_FOUND"


def test_session_lifecycle(c, provider):
    assert start(c).status_code == 200
    dup = start(c)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "SESSION_STATE_INVALID"
    r = c.post("/replay/stop", json={"session_id": "sess_test"}).json()
    assert r["status"] == "stopped"
    assert step(c, provider, HOME).json()["status"] == "stopped"
    assert start(c).status_code == 200  # id reusable once the old session ended
    r = c.post("/replay/step", data={"session_id": "unknown"}, files=upload())
    assert r.status_code == 404 and r.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_step_rejects_invalid_screenshot(c, provider):
    start(c)
    r = c.post("/replay/step", data={"session_id": "sess_test"}, files={"screenshot": ("a.png", b"zz", "image/png")})
    assert r.json()["error"]["code"] == "SCREEN_INVALID"
    assert act(step(c, provider, HOME))["action"] == "TAP"  # session unaffected


def test_generated_session_id(c):
    r = c.post("/replay/start", json={"flow_id": "demo_order", "slots": {"restaurant": "x"}}).json()
    assert r["session_id"].startswith("sess_")


# ----------------------------------------------------------------------------- state machine

def test_transition_table_rejects_illegal_moves(flows_dir):
    from app.models.flow import Flow
    flow = Flow.model_validate(FLOW)
    sess = ReplaySession("s", flow, {}, {})
    with pytest.raises(InvalidTransition):
        sess.transition(S.EXECUTING_ACTION)
    sess.transition(S.LOADING_FLOW)
    sess.transition(S.NEXT_STEP)
    sess.transition(S.RESOLVING_TARGET)
    with pytest.raises(InvalidTransition):
        sess.transition(S.COMPLETED)
    sess.transition(S.STOPPED)
    with pytest.raises(InvalidTransition):
        sess.transition(S.NEXT_STEP)
    assert [h[1] for h in sess.history] == ["LOADING_FLOW", "NEXT_STEP", "RESOLVING_TARGET", "STOPPED"]


def test_terminal_states_have_no_exits_and_gate_only_releases_via_screen():
    for t in (S.COMPLETED, S.FAILED, S.STOPPED):
        assert TRANSITIONS[t] == set()
    assert TRANSITIONS[S.WAITING_FOR_CONFIRMATION] == {S.WAITING_FOR_SCREEN, S.STOPPED, S.FAILED}
    assert S.EXECUTING_ACTION not in TRANSITIONS[S.WAITING_FOR_CONFIRMATION]
