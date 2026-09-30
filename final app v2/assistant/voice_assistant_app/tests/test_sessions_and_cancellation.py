import pytest
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionManager
from voice_assistant_app.turn_manager import TurnManager
from voice_assistant_app.orchestrator import VoiceOrchestrator

@pytest.fixture
def session_mgr(tmp_path):
    wf_mem = WorkflowMemory(json_path=tmp_path / "workflows.json", txt_path=tmp_path / "workflows.txt")
    sess_mgr = SessionManager(base_dir=tmp_path)
    orch = VoiceOrchestrator(memory=wf_mem, session_memory=sess_mgr.get_session("default"))
    turn_mgr = TurnManager(
        orchestrator=orch,
        memory=wf_mem,
        session_memory=sess_mgr.get_session("default"),
        session_manager=sess_mgr
    )
    return turn_mgr, sess_mgr

@pytest.mark.asyncio
async def test_session_isolation(session_mgr):
    turn_mgr, sess_mgr = session_mgr
    
    # Session A sets an intake flow
    res_a1 = await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id="user_alice")
    assert res_a1["decision"]["intent"] == "RESOLVE_MODE"
    assert res_a1["session_id"] == "user_alice"

    # Session B asks something else
    res_b1 = await turn_mgr.process_user_text("Search for wireless earbuds on Amazon", session_id="user_bob")
    assert res_b1["decision"]["intent"] == "RESOLVE_MODE"
    assert res_b1["session_id"] == "user_bob"
    assert "earbuds" in res_b1["spoken_text"].lower()

    # Session A confirms TEACH mode for pizza without being affected by Bob
    res_a2 = await turn_mgr.process_user_text("Teach mode", session_id="user_alice")
    assert res_a2["decision"]["intent"] == "CONFIRM_PLAN"
    assert "margherita" in res_a2["spoken_text"].lower()
    assert "earbuds" not in res_a2["spoken_text"].lower()

@pytest.mark.asyncio
async def test_workflow_cancellation(session_mgr):
    turn_mgr, sess_mgr = session_mgr

    # User initiates order
    res1 = await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id="test_cancel")
    assert res1["decision"]["intent"] == "RESOLVE_MODE"

    # User cancels
    res2 = await turn_mgr.process_user_text("Cancel", session_id="test_cancel")
    assert res2["decision"]["intent"] == "CONVERSE"
    assert "cancelled" in res2["spoken_text"].lower()

    # Verify draft plan is completely wiped
    state = turn_mgr.get_state(session_id="test_cancel")
    assert state["draft_plan"] is None

    # Next query starts completely fresh with no lingering pizza draft
    res3 = await turn_mgr.process_user_text("What can you do?", session_id="test_cancel")
    assert res3["decision"]["intent"] == "CONVERSE"
    assert "learn and replay" in res3["spoken_text"].lower()

@pytest.mark.asyncio
async def test_new_session_reset(session_mgr):
    turn_mgr, sess_mgr = session_mgr

    # Initiate flow
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id="test_reset")
    
    # User triggers new session / reset
    res = await turn_mgr.process_user_text("Start a new session", session_id="test_reset")
    assert res["decision"]["intent"] == "CONVERSE"
    assert "reset" in res["spoken_text"].lower() or "fresh" in res["spoken_text"].lower()

    state = turn_mgr.get_state(session_id="test_reset")
    assert state["draft_plan"] is None
    # Chat history was cleared
    assert len(state["history"]) <= 2  # Only the new session reset exchange

@pytest.mark.asyncio
async def test_intelligent_conversational_questions(session_mgr):
    turn_mgr, sess_mgr = session_mgr

    # Ask about teach mode
    res = await turn_mgr.process_user_text("What is teach mode?", session_id="test_q")
    assert "demonstrate" in res["spoken_text"].lower()
    assert "order mode" in res["spoken_text"].lower()

    # Ask what can you do
    res2 = await turn_mgr.process_user_text("How does this work?", session_id="test_q")
    assert res2["decision"]["intent"] == "CONVERSE"
    assert "automated workflows" in res2["spoken_text"].lower()

@pytest.mark.asyncio
async def test_in_flight_slot_modification(session_mgr):
    turn_mgr, sess_mgr = session_mgr

    # Initiate
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id="test_mod")
    # Resolve mode to teach
    await turn_mgr.process_user_text("Teach mode", session_id="test_mod")
    
    # Modify slot to 2 pizzas
    res_mod = await turn_mgr.process_user_text("Make it two pizzas", session_id="test_mod")
    assert res_mod["decision"]["intent"] == "CONFIRM_PLAN"
    assert "2" in res_mod["spoken_text"] or "two" in res_mod["spoken_text"].lower()

    # Confirm
    res_conf = await turn_mgr.process_user_text("Yes, proceed", session_id="test_mod")
    assert res_conf["decision"]["intent"] == "TEACH"
    assert "learned" in res_conf["spoken_text"].lower()

@pytest.mark.asyncio
async def test_demonstration_finish_lifecycle(session_mgr):
    turn_mgr, sess_mgr = session_mgr
    sess_id = "test_demo_finish"

    # Start teach flow
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    await turn_mgr.process_user_text("Yes, proceed", session_id=sess_id)

    # Finish demonstration via voice cue
    res_fin = await turn_mgr.process_user_text("Finish demonstration", session_id=sess_id)
    assert res_fin["decision"]["intent"] == "CONVERSE"
    assert "demonstration captured and saved" in res_fin["spoken_text"].lower() or "saved" in res_fin["spoken_text"].lower()

    # Verify session draft plan is cleared
    sess_mem = sess_mgr.get_session(sess_id)
    assert sess_mem.get_draft_plan() is None

@pytest.mark.asyncio
async def test_zero_canned_robotic_fallback(session_mgr):
    turn_mgr, _ = session_mgr
    canned_phrase = "i hear you! how can i help you teach a workflow or order something on your phone?"

    queries = [
        "Good morning Ava",
        "Thank you so much",
        "Who is the president of France?",
        "What is your favorite color?"
    ]

    for q in queries:
        res = await turn_mgr.process_user_text(q, session_id="test_canned")
        spoken = res["spoken_text"].lower()
        assert canned_phrase not in spoken, f"Robotic canned phrase returned for query: '{q}'"

