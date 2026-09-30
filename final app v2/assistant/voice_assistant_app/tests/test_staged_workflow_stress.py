import pytest
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionManager
from voice_assistant_app.turn_manager import TurnManager
from voice_assistant_app.orchestrator import VoiceOrchestrator

@pytest.fixture
def staged_env(tmp_path):
    wf_mem = WorkflowMemory(json_path=tmp_path / "workflows.json", txt_path=tmp_path / "workflows.txt")
    sess_mgr = SessionManager(base_dir=tmp_path)
    orch = VoiceOrchestrator(memory=wf_mem, session_memory=sess_mgr.get_session("default"))
    turn_mgr = TurnManager(
        orchestrator=orch,
        memory=wf_mem,
        session_memory=sess_mgr.get_session("default"),
        session_manager=sess_mgr
    )
    return turn_mgr, sess_mgr, wf_mem

@pytest.mark.asyncio
async def test_teach_mode_strict_three_stage_progression(staged_env):
    """
    Test complete sequential progression for TEACH mode:
    Stage 1 (Intent & Mode) -> Stage 2 (3 Slots Verified) -> Stage 3 (Confirm & Demo Recording) -> Stage 4 (Finish & Completed)
    """
    turn_mgr, _, wf_mem = staged_env
    sess_id = "test_teach_staged"

    # Turn 1: Obtain Intent & Intake
    res1 = await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    assert res1["decision"]["intent"] == "RESOLVE_MODE"
    assert res1["current_stage"] == "STAGE_1_INTENT_MODE"
    assert res1["state"] == "AWAITING_MODE"
    assert res1["extracted_plan"]["mode"] == "UNRESOLVED"
    assert res1["extracted_plan"]["stage"] == "STAGE_1_INTENT_MODE"

    # Turn 2: Resolve Mode to TEACH (Slots are already 3/3 complete)
    res2 = await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    assert res2["decision"]["intent"] == "CONFIRM_PLAN"
    assert res2["current_stage"] == "STAGE_3_EXECUTION"
    assert res2["state"] == "AWAITING_CONFIRMATION"
    assert res2["extracted_plan"]["mode"] == "TEACH"
    assert "teach" in res2["spoken_text"].lower()

    # Turn 3: Confirm Plan & Attempt to Learn (Start Demo Recording)
    res3 = await turn_mgr.process_user_text("Yes, start recording", session_id=sess_id)
    assert res3["decision"]["intent"] == "TEACH"
    assert res3["current_stage"] == "STAGE_3_EXECUTION"
    assert res3["state"] == "AWAITING_DEMO"
    assert "learned" in res3["spoken_text"].lower()

    # Turn 4: Finish Demonstration -> Transitions to COMPLETED
    res4 = await turn_mgr.process_user_text("Finish demonstration", session_id=sess_id)
    assert res4["decision"]["intent"] == "CONVERSE"
    assert res4["current_stage"] == "COMPLETED"
    assert "captured and saved" in res4["spoken_text"].lower()

    # Verify workflow is registered in persistent memory
    fid = res3["decision"]["flow_id"]
    saved_flow = wf_mem.get_workflow(fid)
    assert saved_flow is not None
    assert saved_flow["app_name"].lower() == "zomato"

@pytest.mark.asyncio
async def test_order_mode_strict_three_stage_progression(staged_env):
    """
    Test complete sequential progression for ORDER mode:
    Stage 1 (Intent & Mode) -> Stage 2 (3 Slots Verified) -> Stage 3 (Confirm & Autonomous Execution)
    """
    turn_mgr, _, wf_mem = staged_env
    sess_id = "test_order_staged"

    # Turn 1: Direct Order Intent with 3 slots
    res1 = await turn_mgr.process_user_text("Order mode: buy a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    assert res1["decision"]["intent"] == "CONFIRM_PLAN"
    assert res1["current_stage"] == "STAGE_3_EXECUTION"
    assert res1["extracted_plan"]["mode"] == "ORDER"

    # Turn 2: User confirms
    res2 = await turn_mgr.process_user_text("Confirm and proceed", session_id=sess_id)
    assert res2["decision"]["intent"] == "REPLAY"
    assert res2["current_stage"] == "COMPLETED"
    assert "executing" in res2["spoken_text"].lower() or "confirmed" in res2["spoken_text"].lower()

@pytest.mark.asyncio
async def test_stage_two_missing_slots_invariant_no_skipping(staged_env):
    """
    INVARIANT: The engine MUST NOT advance to Stage 3 if Stage 2 (3 canonical slots) is unresolved.
    If restaurant is missing, it must remain in STAGE_2_SLOTS until provided.
    """
    turn_mgr, _, _ = staged_env
    sess_id = "test_missing_slots"

    # Turn 1: Underspecified command ("Order pizza" -> missing restaurant and app)
    res1 = await turn_mgr.process_user_text("Order pizza", session_id=sess_id)
    assert res1["decision"]["intent"] == "AMBIGUITY_RESOLVE"
    assert res1["current_stage"] == "STAGE_2_SLOTS"
    assert res1["extracted_plan"]["slots"].get("item") == "pizza"
    assert not res1["extracted_plan"]["slots"].get("restaurant")

    # Turn 2: User says "Teach mode" without providing missing restaurant
    # Invariant: Must NOT skip to Stage 3! Must remain in STAGE_2_SLOTS!
    res2 = await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    assert res2["decision"]["intent"] == "AMBIGUITY_RESOLVE"
    assert res2["current_stage"] == "STAGE_2_SLOTS"
    assert "restaurant" in res2["spoken_text"].lower() or "domino" in res2["spoken_text"].lower()

    # Turn 3: User now specifies the missing restaurant and app
    res3 = await turn_mgr.process_user_text("From Domino's on Zomato", session_id=sess_id)
    # Now all 3 slots are resolved! And mode was specified as TEACH -> advances to Stage 3!
    assert res3["decision"]["intent"] == "CONFIRM_PLAN"
    assert res3["current_stage"] == "STAGE_3_EXECUTION"
    assert res3["extracted_plan"]["mode"] == "TEACH"
    assert "domino" in str(res3["extracted_plan"]["slots"].get("restaurant", "")).lower()

    # Turn 4: Confirm and start demo
    res4 = await turn_mgr.process_user_text("Yes, proceed", session_id=sess_id)
    assert res4["decision"]["intent"] == "TEACH"
    assert res4["state"] == "AWAITING_DEMO"

@pytest.mark.asyncio
async def test_explicit_teach_with_missing_slots_blocks_stage_three(staged_env):
    """
    When user says 'Teach: order pizza', mode is known (TEACH), but restaurant is missing.
    Must stop at STAGE_2_SLOTS and ask for restaurant before confirming plan.
    """
    turn_mgr, _, _ = staged_env
    sess_id = "test_explicit_teach_missing_slot"

    # Turn 1: Explicit teach but incomplete slots
    res1 = await turn_mgr.process_user_text("Teach: order pizza", session_id=sess_id)
    assert res1["decision"]["intent"] == "AMBIGUITY_RESOLVE"
    assert res1["current_stage"] == "STAGE_2_SLOTS"
    assert res1["extracted_plan"]["mode"] == "TEACH"

    # Turn 2: Provide restaurant
    res2 = await turn_mgr.process_user_text("From Domino's on Zomato", session_id=sess_id)
    assert res2["decision"]["intent"] == "CONFIRM_PLAN"
    assert res2["current_stage"] == "STAGE_3_EXECUTION"
    assert res2["extracted_plan"]["mode"] == "TEACH"

@pytest.mark.asyncio
async def test_strict_non_regression_after_completion(staged_env):
    """
    INVARIANT: Once all stages are done (COMPLETED), the orchestrator MUST NOT regress
    to asking previous stage questions ('Which restaurant?' or 'Are you in teach or order mode?'),
    unless the user explicitly reiterates something.
    """
    turn_mgr, _, _ = staged_env
    sess_id = "test_non_regression"

    # Run through full workflow to COMPLETED
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    await turn_mgr.process_user_text("Yes, start recording", session_id=sess_id)
    res_fin = await turn_mgr.process_user_text("Finish demonstration", session_id=sess_id)
    assert res_fin["current_stage"] == "COMPLETED"

    # Turn 5: Casual thank you
    # STRICT NON-REGRESSION: Do NOT ask for mode or restaurant!
    res5 = await turn_mgr.process_user_text("Thank you Ava", session_id=sess_id)
    assert res5["decision"]["intent"] == "CONVERSE"
    assert res5["current_stage"] == "COMPLETED"
    assert "which restaurant" not in res5["spoken_text"].lower()
    assert "teach mode or order mode" not in res5["spoken_text"].lower()
    assert "welcome" in res5["spoken_text"].lower()

    # Turn 6: Casual greeting
    res6 = await turn_mgr.process_user_text("Hello again", session_id=sess_id)
    assert res6["decision"]["intent"] == "CONVERSE"
    assert res6["current_stage"] == "COMPLETED"
    assert "which restaurant" not in res6["spoken_text"].lower()

    # Turn 7: Ask capabilities
    res7 = await turn_mgr.process_user_text("What can you do?", session_id=sess_id)
    assert res7["decision"]["intent"] == "CONVERSE"
    assert res7["current_stage"] == "COMPLETED"
    assert "automated workflows" in res7["spoken_text"].lower()

@pytest.mark.asyncio
async def test_reiteration_on_completed_workflow(staged_env):
    """
    EXCEPTION TO NON-REGRESSION: If the user explicitly reiterates or modifies a parameter
    of the completed workflow (e.g. 'Actually make it two pizzas' or 'Deliver to work'),
    the workflow is re-opened into STAGE_3_EXECUTION with the updated slots!
    """
    turn_mgr, _, _ = staged_env
    sess_id = "test_reiteration"

    # Complete initial flow
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    await turn_mgr.process_user_text("Yes, proceed", session_id=sess_id)
    await turn_mgr.process_user_text("Finish demonstration", session_id=sess_id)

    # Reiteration 1: Modify quantity on completed workflow
    res_mod1 = await turn_mgr.process_user_text("Actually make it two pizzas", session_id=sess_id)
    assert res_mod1["decision"]["intent"] == "CONFIRM_PLAN"
    assert res_mod1["current_stage"] == "STAGE_3_EXECUTION"
    assert res_mod1["extracted_plan"]["slots"].get("quantity") == 2
    assert "2" in res_mod1["spoken_text"] or "two" in res_mod1["spoken_text"].lower()

    # Reiteration 2: Modify item on completed workflow
    res_mod2 = await turn_mgr.process_user_text("Change it to Farmhouse pizza", session_id=sess_id)
    assert res_mod2["decision"]["intent"] == "CONFIRM_PLAN"
    assert res_mod2["current_stage"] == "STAGE_3_EXECUTION"
    assert "farmhouse" in str(res_mod2["extracted_plan"]["slots"].get("item", "")).lower()

    # User confirms the updated plan
    res_conf = await turn_mgr.process_user_text("Yes, proceed", session_id=sess_id)
    assert res_conf["decision"]["intent"] == "TEACH"
    assert res_conf["state"] == "AWAITING_DEMO"

@pytest.mark.asyncio
async def test_in_flight_multi_slot_overrides(staged_env):
    """
    Test multiple in-flight slot reiterations while awaiting plan confirmation.
    """
    turn_mgr, _, _ = staged_env
    sess_id = "test_in_flight_overrides"

    # Start intake and resolve mode
    await turn_mgr.process_user_text("Order a Margherita pizza from Domino's on Zomato", session_id=sess_id)
    res_teach = await turn_mgr.process_user_text("Teach mode", session_id=sess_id)
    assert res_teach["current_stage"] == "STAGE_3_EXECUTION"

    # Override 1: Farmhouse
    res1 = await turn_mgr.process_user_text("Actually Farmhouse pizza", session_id=sess_id)
    assert res1["decision"]["intent"] == "CONFIRM_PLAN"
    assert "farmhouse" in str(res1["extracted_plan"]["slots"].get("item", "")).lower()

    # Override 2: Deliver to work
    res2 = await turn_mgr.process_user_text("Deliver to work address", session_id=sess_id)
    assert res2["decision"]["intent"] == "CONFIRM_PLAN"
    assert "work" in str(res2["extracted_plan"]["slots"].get("address", "")).lower()

    # Override 3: Quantity 2
    res3 = await turn_mgr.process_user_text("Make it two", session_id=sess_id)
    assert res3["decision"]["intent"] == "CONFIRM_PLAN"
    assert res3["extracted_plan"]["slots"].get("quantity") == 2

    # Confirm
    res_final = await turn_mgr.process_user_text("Yes, go ahead", session_id=sess_id)
    assert res_final["decision"]["intent"] == "TEACH"
    assert res_final["decision"]["extracted_slots"]["quantity"] == 2
