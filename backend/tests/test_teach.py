"""TEACH conversion uses only visual evidence and produces a generic template."""
import json

GROUNDED = [
    {"step_index": 0, "type": "tap", "tap_x": 320, "tap_y": 2600, "source": "window_change",
     "element": {"text": "Zomato", "resourceId": "com.launcher:id/icon", "className": "TextView",
                 "bounds": [0, 0, 1, 1]},
     "grounded_element": {"id": 3, "text": "Zomato", "bbox": [200, 2550, 440, 2650], "center": [320, 2600],
                          "grounded_confidence": "contained"}},
    {"step_index": 1, "type": "tap", "tap_x": 732, "tap_y": 394, "source": "click",
     "element": {"text": 'Search "comfort food"', "resourceId": "com.application.zomato:id/search"},
     "grounded_element": {"id": 14, "text": 'Search "binge night"', "bbox": [100, 350, 1100, 440],
                          "center": [600, 395], "grounded_confidence": "contained"},
     "before_text": ["Home"], "after_text": ["Restaurant name or a dish"]},
    {"step_index": 2, "type": "type_text", "text": "pizz"},
    {"step_index": 3, "type": "tap", "tap_x": 846, "tap_y": 1761, "source": "window_change",
     "element": {"text": "Zomato"},
     "grounded_element": {"id": 30, "text": "Pizza Hut Flat 100 OFF", "bbox": [60, 1700, 1150, 2120],
                          "center": [605, 1911], "grounded_confidence": "contained"},
     "after_text": ["Pizza Hut", "Search in Pizza Hut"]},
    {"step_index": 4, "type": "tap", "tap_x": 1037, "tap_y": 1124, "source": "click",
     "element": {"text": "American Nashville Chicken Pizza", "bounds": [700, 1000, 1200, 1300]},
     "grounded_element": {"id": None, "text": "SHOULD NOT BE USED", "bbox": [700, 1000, 1200, 1300],
                          "center": [950, 1150], "grounded_confidence": "log_bounds",
                          "inner_element": {"id": 29, "text": "ADD", "bbox": [770, 1340, 1141, 1466],
                                            "center": [955, 1403]}}},
    {"step_index": 5, "type": "tap", "tap_x": 788, "tap_y": 2517, "source": "click",
     "grounded_element": {"id": 28, "text": "Add item ₹598", "bbox": [400, 2480, 1180, 2620],
                          "center": [790, 2550], "grounded_confidence": "contained"}},
    {"step_index": 6, "type": "tap", "tap_x": 600, "tap_y": 2550, "source": "click",
     "grounded_element": {"id": 40, "text": "Place Order ₹357", "bbox": [40, 2480, 1180, 2620],
                          "center": [610, 2550], "grounded_confidence": "contained"}},
    {"step_index": 7, "type": "tap", "tap_x": 910, "tap_y": 1750, "source": "click",
     "grounded_element": {"id": 1, "text": "Stop", "bbox": [800, 1700, 1000, 1800], "center": [900, 1750],
                          "grounded_confidence": "contained"}},
]

REQ = {"flow_id": "order_item_zomato", "app": "Zomato", "description": "Order an item from a restaurant on Zomato",
       "slots": {"cuisine": "pizza", "restaurant": "Pizza Hut", "item": "American Nashville Chicken Pizza"},
       "grounded_steps": GROUNDED, "first_step_index": 1, "last_step_index": 6, "use_llm": False}


def test_teach_builds_semantic_template(client):
    r = client.post("/flows/teach", json=REQ)
    assert r.status_code == 200, r.text
    body = r.json()
    flow = body["flow"]
    steps = flow["steps"]
    dumped = json.dumps(flow)
    # No coordinates, no accessibility data, nothing from the log_bounds pseudo-element.
    for banned in ("tap_x", '"x"', "resourceId", "className", "bounds", "SHOULD NOT BE USED", "comfort food", "1761"):
        assert banned not in dumped
    assert [s["action"] for s in steps] == ["LAUNCH_APP", "TAP", "TYPE", "TAP", "TAP", "TAP", "TAP"]
    assert steps[1]["target"]["semantic_role"] == "search_field" and steps[1]["target"]["text"] is None
    assert steps[2]["value"] == "{{cuisine}}"  # partial typing "pizz" generalized to the slot
    assert steps[3]["target"]["slot"] == "restaurant" and steps[3]["target"]["text"] == "{{restaurant}}"
    assert steps[3]["verify"] == {"mode": "anchors", "anchors": ["{{restaurant}}"]}
    assert steps[4]["target"]["semantic_role"] == "add_item" and steps[4]["target"]["text"] == "ADD"
    assert steps[4]["target"]["slot"] == "restaurant"  # last slot seen (item wasn't on screen text)
    assert steps[5]["optional"] is True and steps[5]["target"]["text"] == "Add item"
    assert steps[6]["final_confirmation"] is True and steps[6]["target"]["semantic_role"] == "place_order"
    assert set(flow["required_slots"]) == {"cuisine", "restaurant"} and flow["source"] == "teach"
    assert body["saved"] is False


def test_teach_save_and_replay_start(client):
    r = client.post("/flows/teach", json={**REQ, "save": True})
    assert r.json()["saved"] is True
    assert client.get("/flows/order_item_zomato").status_code == 200
    assert client.post("/flows/teach", json={**REQ, "save": True}).json()["error"]["code"] == "FLOW_EXISTS"
    r = client.post("/replay/start", json={"flow_id": "order_item_zomato",
                                           "slots": {"cuisine": "biryani", "restaurant": "Paradise"}})
    assert r.json()["action"]["action"] == "LAUNCH_APP"


def test_teach_llm_descriptions_cannot_invent_slots(make_client, fake_llm):
    c = make_client(llm=True)
    fake_llm.push(json.dumps({"steps": [
        {"description": "the search bar at the top", "semantic_role": "search_field"},
        {"description": "restaurant card for {{restaurant}}", "semantic_role": "restaurant_result"},
        {"description": "ADD button for {{secret_slot}}", "semantic_role": "add_item"},
        {"description": "Add item on sheet", "semantic_role": "x"},
        {"description": "Place order", "semantic_role": "place_order"}]}))
    body = c.post("/flows/teach", json={**REQ, "use_llm": True}).json()
    steps = body["flow"]["steps"]
    assert body["llm_used"] is True
    assert steps[1]["target"]["description"] == "the search bar at the top"
    assert steps[3]["target"]["description"] == "restaurant card for {{restaurant}}"
    assert "secret_slot" not in json.dumps(body["flow"])


def test_teach_rejects_empty_range(client):
    r = client.post("/flows/teach", json={**REQ, "first_step_index": 50})
    assert r.status_code == 422
