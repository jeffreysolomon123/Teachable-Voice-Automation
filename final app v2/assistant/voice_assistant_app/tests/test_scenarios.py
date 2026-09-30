import pytest
from voice_assistant_app.config import DEFAULT_VOICE
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionMemory
from voice_assistant_app.orchestrator import VoiceOrchestrator
from voice_assistant_app.turn_manager import TurnManager

@pytest.fixture(scope="module")
def temp_memory(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("test_voice_data")
    json_path = temp_dir / "workflows.json"
    txt_path = temp_dir / "workflows.txt"
    mem = WorkflowMemory(json_path=json_path, txt_path=txt_path)
    return mem

@pytest.fixture(scope="module")
def temp_session(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("test_session_data")
    json_path = temp_dir / "session.json"
    txt_path = temp_dir / "user_context.txt"
    sess = SessionMemory(json_path=json_path, txt_path=txt_path)
    return sess

@pytest.fixture(scope="module")
def turn_mgr(temp_memory, temp_session):
    orch = VoiceOrchestrator(memory=temp_memory, session_memory=temp_session)
    return TurnManager(orchestrator=orch, memory=temp_memory, session_memory=temp_session)

def test_default_voice_is_ava():
    """Verify Ava is the default voice across configuration and TTS service."""
    assert DEFAULT_VOICE == "en-US-AvaMultilingualNeural"
    from voice_assistant_app.tts_service import TTSService
    tts = TTSService()
    assert tts.voice == "en-US-AvaMultilingualNeural"

@pytest.mark.asyncio
async def test_t1_starter_intake_mode_resolution_and_plan_confirmation(turn_mgr):
    """
    Starter conversation:
    1. User requests unlearned action -> Assistant extracts slots and asks to resolve TEACH vs ORDER mode.
    2. User resolves mode to 'Teach' -> Assistant outputs structured JSON plan awaiting confirmation.
    3. User confirms 'Yes, proceed' -> Flow is learned and recorded in memory.
    """
    # Turn 1: Initial user utterance without explicit mode
    utterance = "Order a Margherita pizza from Domino's on Zomato"
    res1 = await turn_mgr.process_user_text(utterance)
    
    assert res1["decision"]["intent"] == "RESOLVE_MODE"
    assert res1["matched_testcase"] == "T1"
    assert res1["state"] == "AWAITING_MODE"
    assert "teach mode" in res1["spoken_text"].lower() and "order mode" in res1["spoken_text"].lower()
    
    # Verify structured JSON output extracted from starter conversation
    plan1 = res1["extracted_plan"]
    assert plan1 is not None
    assert plan1["mode"] == "UNRESOLVED"
    assert plan1["status"] == "awaiting_mode"
    assert "margherita" in str(plan1["slots"].get("item", "")).lower()
    assert "domino" in str(plan1["slots"].get("restaurant", "")).lower()
    assert "zomato" in str(plan1["slots"].get("app", "")).lower()

    # Turn 2: User resolves mode to TEACH
    res2 = await turn_mgr.process_user_text("Teach mode")
    assert res2["decision"]["intent"] == "CONFIRM_PLAN"
    assert res2["matched_testcase"] == "T1"
    assert res2["state"] == "AWAITING_CONFIRMATION"
    assert "teach" in res2["spoken_text"].lower()
    
    plan2 = res2["extracted_plan"]
    assert plan2 is not None
    assert plan2["mode"] == "TEACH"
    assert plan2["status"] == "awaiting_confirmation"
    assert plan2["confirmed"] is False

    # Turn 3: User confirms plan
    res3 = await turn_mgr.process_user_text("Yes, proceed")
    assert res3["decision"]["intent"] == "TEACH"
    assert res3["matched_testcase"] == "T1"
    assert res3["state"] == "AWAITING_DEMO"
    assert "learned" in res3["spoken_text"].lower()
    
    # Verify saved in memory
    fid = res3["decision"]["flow_id"]
    saved = turn_mgr.memory.get_workflow(fid)
    assert saved is not None
    assert saved["app_name"].lower() == "zomato"

@pytest.mark.asyncio
async def test_t2_exact_replay(turn_mgr):
    utterance = "Order a Margherita pizza from Domino's on Zomato"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert "zomato" in res["decision"]["matched_flow_id"].lower()
    assert res["matched_testcase"] in ["T2", "T3"]
    assert res["extracted_plan"]["mode"] == "ORDER"

@pytest.mark.asyncio
async def test_t3_paraphrase(turn_mgr):
    utterance = "Get me a margherita from dominos"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert "zomato" in res["decision"]["matched_flow_id"].lower()
    assert res["matched_testcase"] in ["T2", "T3"]

@pytest.mark.asyncio
async def test_t4_slot_item(turn_mgr):
    utterance = "Order a Farmhouse pizza from Domino's on Zomato"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert res["matched_testcase"] == "T4"
    slots = res["decision"]["slot_overrides"]
    assert "item" in slots
    assert "farmhouse" in str(slots["item"]).lower()

@pytest.mark.asyncio
async def test_t5_slot_quantity(turn_mgr):
    utterance = "Order two Margherita pizzas from Domino's"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert res["matched_testcase"] == "T5"
    slots = res["decision"]["slot_overrides"]
    assert "quantity" in slots
    assert slots["quantity"] == 2 or slots["quantity"] == "2"

@pytest.mark.asyncio
async def test_t6_slot_address(turn_mgr):
    utterance = "Order a Margherita from Domino's, deliver to work"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert res["matched_testcase"] == "T6"
    slots = res["decision"]["slot_overrides"]
    assert "address" in slots
    assert "work" in str(slots["address"]).lower()

@pytest.mark.asyncio
async def test_t8_teach_ecommerce_explicit_mode(turn_mgr):
    # Direct teach command
    utterance = "Teach: Search for wireless earbuds on Amazon and add the first result to cart"
    res1 = await turn_mgr.process_user_text(utterance)
    assert res1["decision"]["intent"] == "CONFIRM_PLAN"
    assert res1["matched_testcase"] == "T8"
    assert res1["extracted_plan"]["mode"] == "TEACH"
    
    # Confirm
    res2 = await turn_mgr.process_user_text("Yes, start recording")
    assert res2["decision"]["intent"] == "TEACH"
    assert res2["matched_testcase"] == "T8"
    assert "amazon" in res2["decision"]["app_name"].lower()

@pytest.mark.asyncio
async def test_t9_cross_app_slot(turn_mgr):
    utterance = "Search for a phone case on Amazon and add the first result to cart"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "REPLAY"
    assert res["matched_testcase"] == "T9"
    slots = res["decision"]["slot_overrides"]
    assert any("phone case" in str(v).lower() for v in slots.values())

@pytest.mark.asyncio
async def test_t12_unknown_intent(turn_mgr):
    utterance = "Book a cab to the airport"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "UNKNOWN"
    assert res["matched_testcase"] == "T12"
    assert "haven't learned" in res["spoken_text"].lower() or "teach" in res["spoken_text"].lower()

@pytest.mark.asyncio
async def test_t13_ambiguity(turn_mgr):
    utterance = "Order pizza"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "AMBIGUITY_RESOLVE"
    assert res["matched_testcase"] == "T13"
    assert "?" in res["spoken_text"]

@pytest.mark.asyncio
async def test_t14_reporting(turn_mgr):
    turn_mgr.memory.record_run_result(
        flow_id="order_dominos_zomato",
        status="SUCCESS_UP_TO_PAYMENT",
        step_stopped="payment_screen",
        details="Stopped before credential capture"
    )
    utterance = "Did the last run succeed?"
    res = await turn_mgr.process_user_text(utterance)
    assert res["decision"]["intent"] == "STATUS_QUERY"
    assert res["matched_testcase"] == "T14"
    assert "payment" in res["spoken_text"].lower() or "success" in res["spoken_text"].lower()

@pytest.mark.asyncio
async def test_conversational_memory_and_preferences(turn_mgr):
    # Turn 1: User sets preference
    t1 = await turn_mgr.process_user_text("Remember that my office is in Cyber City")
    assert t1["decision"]["intent"] == "CONVERSE"
    
    # Turn 2: User asks about the preference
    t2 = await turn_mgr.process_user_text("Where is my office?")
    assert t2["decision"]["intent"] == "CONVERSE"
    assert "cyber city" in t2["spoken_text"].lower() or "office" in t2["spoken_text"].lower()

def test_interventions(turn_mgr):
    # Test T11 Credential handoff
    handoff = turn_mgr.trigger_execution_event("credential_boundary")
    assert handoff["current_state"] == "HANDOFF_PAUSED"
    assert "payment" in handoff["spoken_prompt"].lower() or "turn" in handoff["spoken_prompt"].lower()

    # Test T10 Stuck state
    stuck = turn_mgr.trigger_execution_event("stuck_state", details="Screen in Hindi")
    assert stuck["current_state"] == "AWAITING_CLARIFICATION"
    assert "hindi" in stuck["spoken_prompt"].lower()
