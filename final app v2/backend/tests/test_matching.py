import pytest

from app.models.flow import Target
from app.models.segment import Element, Screen
from app.services.matching_service import bbox_center, match_deterministic, norm, text_score

from .conftest import H, W


def E(i, text, bbox, type_="text"):
    return Element(index=i, text=text, bbox=bbox, type=type_)


RESTAURANTS = [
    E(0, "Search for restaurants", (40, 100, 1030, 180)),
    E(1, "Domino's", (80, 450, 500, 530)),
    E(2, "Pizza Hut", (80, 620, 500, 700)),
    E(3, "Burger King", (80, 790, 500, 870)),
]


def match(target, elements, slots=None, **kw):
    kw = {"width": W, "height": H, "confident": 0.8, "margin": 0.08, "min_candidate": 0.45, **kw}
    return match_deterministic(Target(**target), elements, slots or {}, **kw)


def test_norm():
    assert norm("Domino’s - Pizza!") == "dominos pizza"
    assert norm("Near & Fast") == "near and fast"


def test_exact_match_and_center_coordinates():
    r = match({"description": "tap the Domino's restaurant", "text": "Domino's"}, RESTAURANTS)
    assert r.found and r.index == 1 and r.method == "deterministic"
    assert (r.x, r.y) == (290, 490) == bbox_center((80, 450, 500, 530))


def test_normalized_match_ignores_punctuation_and_case():
    els = [E(0, "DOMINOS PIZZA", (0, 300, 400, 360)), E(1, "Pizza Hut", (0, 500, 400, 560))]
    r = match({"description": "restaurant", "slot": "restaurant"}, els, {"restaurant": "Domino's Pizza"})
    assert r.found and r.index == 0 and r.confidence >= 0.97


def test_fuzzy_match_tolerates_ocr_noise():
    els = [E(0, "Domino's Pizze", (0, 300, 400, 360)), E(1, "Burger King", (0, 500, 400, 560))]
    r = match({"description": "restaurant", "slot": "restaurant"}, els, {"restaurant": "Domino's Pizza"})
    assert r.found and r.index == 0
    assert text_score("margherita pizza", "margherlta pizza") > 0.8
    # Heavier noise is ranked first but not trusted: it goes to the text LLM tier.
    els[0] = E(0, "Dornino's Pizze", (0, 300, 400, 360))
    r = match({"description": "restaurant", "slot": "restaurant"}, els, {"restaurant": "Domino's Pizza"})
    assert not r.found and r.candidates[0]["index"] == 0


def test_description_phrase_is_used_when_no_slot():
    r = match({"description": "restaurant matching Pizza Hut"}, RESTAURANTS)
    assert r.found and r.index == 2


def test_semantic_role_match_for_search_field():
    els = [E(0, "Deliver to Home", (40, 60, 600, 120)), E(1, 'Search "biryani"', (40, 200, 1000, 280)),
           E(2, "Recommended for you", (40, 900, 700, 960))]
    r = match({"description": "search field", "semantic_role": "search_field"}, els)
    assert r.found and r.index == 1


def test_ambiguous_candidates_are_not_auto_selected():
    els = [E(0, "Domino's Pizza", (0, 300, 500, 360)), E(1, "Domino's Express", (0, 900, 500, 960)),
           E(2, "Pizza Hut", (0, 1500, 500, 1560))]
    r = match({"description": "restaurant matching Domino's", "slot": "restaurant"}, els, {"restaurant": "Domino's"})
    assert not r.found and r.ambiguous and {c["index"] for c in r.candidates[:2]} == {0, 1}


def test_spec_example_list_is_ambiguous_but_ranked():
    els = [E(0, "Domino's Pizza", (0, 300, 500, 360)), E(1, "Pizza Hut", (0, 700, 500, 760)),
           E(2, "Domino's - Perungudi", (0, 1100, 500, 1160)), E(3, "Burger King", (0, 1500, 500, 1560))]
    r = match({"description": "restaurant matching Domino's", "slot": "restaurant"}, els, {"restaurant": "Domino's"})
    assert not r.found and r.ambiguous
    assert [c["index"] for c in r.candidates[:2]] == [0, 2]


def test_nested_boxes_with_same_text_are_not_rivals():
    els = [E(0, "Domino's Pizza 4.3 30 mins", (0, 280, 1000, 600), "container"),
           E(1, "Domino's Pizza", (20, 300, 500, 360))]
    r = match({"description": "restaurant", "slot": "restaurant"}, els, {"restaurant": "Domino's Pizza"})
    assert r.found and r.index == 1


def test_relational_add_button_near_item():
    els = [E(0, "Margherita", (30, 400, 500, 450)), E(1, "ADD", (770, 560, 1040, 640), "labeled_element"),
           E(2, "Farmhouse", (30, 1300, 500, 1350)), E(3, "ADD", (770, 1460, 1040, 1540), "labeled_element")]
    r = match({"description": "Add button for {{item}}", "semantic_role": "add_item", "slot": "item", "text": "ADD"},
              els, {"item": "Farmhouse"})
    assert r.found and r.index == 3
    r = match({"description": "Add button", "semantic_role": "add_item", "slot": "item", "text": "ADD"},
              els, {"item": "Margherita"})
    assert r.found and r.index == 1


def test_no_match_returns_not_found():
    r = match({"description": "restaurant", "slot": "restaurant"}, RESTAURANTS, {"restaurant": "Subway"})
    assert not r.found and r.method == "none"


def test_status_bar_is_ignored():
    els = [E(0, "Search", (40, 10, 200, 50)), E(1, "Home", (40, 900, 200, 960))]
    assert not match({"description": "search field", "semantic_role": "search_field"}, els).found


def test_region_hint_breaks_ties():
    els = [E(0, "Continue", (40, 300, 400, 360)), E(1, "Continue", (40, 2200, 400, 2260))]
    r = match({"description": "continue", "text": "Continue", "region": "bottom"}, els)
    assert r.found and r.index == 1


def test_bbox_validation_rejects_inverted_boxes():
    with pytest.raises(ValueError):
        E(0, "x", (10, 10, 5, 20))


def test_match_endpoint(client):
    body = {"target": {"description": "restaurant matching Domino's", "semantic_role": "restaurant"},
            "elements": [e.model_dump() for e in RESTAURANTS]}
    r = client.post("/match", json=body).json()
    assert r["found"] and r["index"] == 1 and r["method"] == "deterministic" and (r["x"], r["y"]) == (290, 490)
    body["elements"][0]["index"] = 5
    assert client.post("/match", json=body).json()["error"]["code"] == "INVALID_REQUEST"


def test_screen_all_text():
    s = Screen(width=W, height=H, elements=RESTAURANTS + [E(4, "", (0, 0, 10, 10), "icon_or_image")])
    assert len(s.all_text) == 4
