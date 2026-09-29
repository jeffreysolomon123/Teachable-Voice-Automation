import copy

import pytest
from pydantic import ValidationError

from app.models.flow import Flow
from app.services.flow_service import clean_value, materialize_step, slot_text_values, substitute

SLOT_JSON = {
    "mode": "TEACH", "stage": "STAGE_2_SLOTS", "status": "awaiting_confirmation",
    "flow_id": "order_dominos_zomato", "app_name": "Zomato",
    "summary": "Order a Margherita pizza from Domino's on Zomato", "confirmed": False,
    "slots": {"item": "Margherita pizza", "restaurant": "Domino's", "app": "Zomato", "quantity": 1,
              "address": "Home", "customizations": None},
    "slot_status": {
        "item": {"value": "Margherita pizza", "status": "FILLED", "confidence": 0.98},
        "restaurant": {"value": "Domino's", "status": "FILLED", "confidence": 0.99},
        "app": {"value": "Zomato", "status": "FILLED", "confidence": 0.95},
        "quantity": {"value": 1, "status": "DEFAULT", "confidence": 1.0},
        "address": {"value": "Home", "status": "USER_PREFERENCE", "confidence": 0.92}},
    "missing_slots": [],
    "metadata": {"session_id": "sess_89f1c42b", "timestamp": "2026-09-29T19:40:00Z",
                 "raw_query": "Teach me how to order a Margherita pizza from Domino's on Zomato",
                 "requires_user_confirmation": True},
}


# ----------------------------------------------------------------------------- slot validation

def test_validate_supplied_slot_json(client):
    r = client.post("/slots/validate", json=SLOT_JSON)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["missing_slots"] == [] and body["requires_confirmation"] is True
    assert body["task"] == {"goal": "Order a Margherita pizza from Domino's on Zomato", "app": "Zomato",
                            "variables": {"item": "Margherita pizza", "restaurant": "Domino's", "quantity": 1,
                                          "address": "Home", "customizations": None}}


def test_confirmed_payload_needs_no_confirmation(client):
    p = copy.deepcopy(SLOT_JSON)
    p["confirmed"] = True
    assert client.post("/slots/validate", json=p).json()["requires_confirmation"] is False


def test_missing_slots_reported(client):
    p = copy.deepcopy(SLOT_JSON)
    p["slots"]["item"] = None
    p["slot_status"]["item"] = {"value": None, "status": "MISSING", "confidence": 0.0}
    p["missing_slots"] = ["address2"]
    p["flow_id"] = "order_food_zomato"  # stored flow requires item
    body = client.post("/slots/validate", json=p).json()
    assert body["valid"] is False
    assert set(body["missing_slots"]) == {"item", "address2"}


def test_low_confidence_slot_requires_confirmation(client):
    p = copy.deepcopy(SLOT_JSON)
    p["confirmed"] = True
    p["slot_status"]["restaurant"]["confidence"] = 0.3
    body = client.post("/slots/validate", json=p).json()
    assert body["low_confidence_slots"] == ["restaurant"] and body["requires_confirmation"] is True


def test_invalid_slot_payload_uses_error_format(client):
    p = copy.deepcopy(SLOT_JSON)
    p["slot_status"]["item"]["confidence"] = 7
    r = client.post("/slots/validate", json=p)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_SLOTS"
    assert set(r.json()["error"]) == {"code", "message", "retryable"}


# ----------------------------------------------------------------------------- substitution

def test_substitution_is_single_pass_and_literal():
    assert substitute("food item matching {{item}}", {"item": "Margherita pizza"}) == "food item matching Margherita pizza"
    assert substitute("{{ item }}!", {"item": "{{restaurant}}"}) == "{{restaurant}}!"
    assert substitute("a\\1 {{x}}", {"x": r"\g<0> $1"}) == r"a\1 \g<0> $1"
    with pytest.raises(KeyError):
        substitute("{{nope}}", {})


def test_clean_value_strips_control_chars_and_bounds_length():
    assert clean_value("Dom\ninos\t Pizza\x00", 100) == "Dom inos Pizza"
    assert clean_value(1.0, 10) == "1" and clean_value(True, 10) == "yes"
    assert len(clean_value("x" * 500, 50)) == 50


def test_materialize_skips_steps_with_empty_optional_slot(flows_dir):
    flow = Flow.model_validate_json((flows_dir / "order_food_zomato.json").read_text())
    flow.steps[2].value = "{{customizations}}"
    values = slot_text_values(flow, {"restaurant": "Domino's", "item": "Margherita", "customizations": None}, 200)
    assert materialize_step(flow.steps[2], flow, values) is None
    step4 = materialize_step(flow.steps[3], flow, values)
    assert step4.target.description == "restaurant matching Domino's" and step4.target.text == "Domino's"
    assert step4.verify.anchors == ["Domino's"]
    assert flow.steps[3].target.text == "{{restaurant}}"  # template untouched


# ----------------------------------------------------------------------------- flow model / storage

def _flow(**kw):
    base = {"flow_id": "demo_flow", "app": "Zomato", "description": "demo", "required_slots": ["item"],
            "steps": [{"step": 1, "action": "LAUNCH_APP", "app": "Zomato"},
                      {"step": 2, "action": "TAP", "target": {"description": "item {{item}}", "slot": "item"}},
                      {"step": 3, "action": "TYPE", "value": "{{item}}"}]}
    base.update(kw)
    return base


def test_flow_rejects_coordinates_and_undeclared_slots():
    bad = _flow()
    bad["steps"][1]["x"] = 534
    with pytest.raises(ValidationError):
        Flow.model_validate(bad)
    bad = _flow()
    bad["steps"][1]["target"]["x"] = 10
    with pytest.raises(ValidationError):
        Flow.model_validate(bad)
    with pytest.raises(ValidationError):
        Flow.model_validate(_flow(required_slots=[]))
    with pytest.raises(ValidationError):
        Flow.model_validate(_flow(steps=[{"step": 2, "action": "BACK"}]))
    with pytest.raises(ValidationError):
        Flow.model_validate(_flow(flow_id="../etc/passwd"))


def test_create_list_get_flow(client):
    r = client.post("/flows", json=_flow())
    assert r.status_code == 201
    assert client.post("/flows", json=_flow()).json()["error"]["code"] == "FLOW_EXISTS"
    assert client.post("/flows?overwrite=true", json=_flow(description="v2")).status_code == 201
    ids = {f["flow_id"] for f in client.get("/flows").json()}
    assert {"demo_flow", "order_food_zomato"} <= ids
    got = client.get("/flows/demo_flow").json()
    assert got["description"] == "v2" and got["steps"][1]["target"]["slot"] == "item"
    r = client.get("/flows/missing_flow")
    assert r.status_code == 404 and r.json()["error"]["code"] == "FLOW_NOT_FOUND"
    assert client.get("/flows/..%2F..%2Fsecret").status_code == 404


# ----------------------------------------------------------------------------- flow matching

def test_flow_matching_picks_generic_flow(client):
    client.post("/flows", json=_flow(flow_id="order_groceries_flipkart", app="Flipkart",
                                     description="Buy groceries on Flipkart"))
    r = client.post("/flows/match", json=SLOT_JSON).json()
    assert r["found"] is True and r["flow_id"] == "order_food_zomato" and r["method"] == "deterministic"
    assert r["missing_slots"] == []


def test_flow_matching_wrong_app_finds_nothing(client):
    p = copy.deepcopy(SLOT_JSON)
    p["app_name"] = "Swiggy"
    p["slots"]["app"] = "Swiggy"
    assert client.post("/flows/match", json=p).json()["found"] is False


def test_flow_matching_uses_llm_when_ambiguous(make_client, fake_llm):
    c = make_client(llm=True)
    c.post("/flows", json=_flow(flow_id="reorder_food_zomato", description="Order food again from Zomato",
                                required_slots=["item"], optional_slots=["restaurant"]))
    p = copy.deepcopy(SLOT_JSON)
    p["flow_id"] = None
    p["summary"] = "get me a margherita"
    fake_llm.push('{"flow_id": "order_food_zomato", "confidence": 0.9}')
    r = c.post("/flows/match", json=p).json()
    assert r["found"] and r["flow_id"] == "order_food_zomato" and r["method"] == "text_llm"
