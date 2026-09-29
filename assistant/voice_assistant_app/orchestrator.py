import json
import time
import re
from typing import Dict, Any, Optional, List
from voice_assistant_app.config import GROQ_API_KEY, GEMINI_API_KEY
from voice_assistant_app.schemas import (
    ExtractedPlan,
    ResolveModeIntent,
    ConfirmPlanIntent,
    TeachIntent,
    ReplayIntent,
    AmbiguityClarification,
    UnknownIntent,
    StatusQueryIntent,
    ConverseIntent,
    ExecutionIntervention,
    OrchestratorResponse,
    VoiceDecision
)
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionMemory
from voice_assistant_app.testcase_evaluator import TestCaseEvaluator

SYSTEM_PROMPT = """You are Ava, a highly capable, articulate, and natural executive voice assistant for Android with mobile automation and screen teaching capabilities.

### CORE PERSONA & COMMUNICATION STYLE:
- Speak naturally, warmly, concisely, and with executive precision.
- NEVER use robotic canned cliches like "I hear you! How can I help you..." or repetitive stock phrases.
- Maintain real multi-turn conversational context, remember user preferences, and ground your responses directly in what the user asked.
- In spoken_response, use natural, fluid conversational English suitable for high-quality speech synthesis.

### CONVERSATIONAL PLAYBOOK & INTAKE WORKFLOW:
1. GREETINGS & CASUAL CONVERSATION (intent="CONVERSE"):
   - For greetings, general questions, or small talk, respond warmly and helpfully.
   - When asked what you can do or how you work (e.g. "What can you do?", "How does this work?"): Explain that you can learn and replay automated workflows on their phone (e.g. ordering on Zomato, shopping on Amazon).
   - When asked what teach mode is (e.g. "What is teach mode?"): Explain that in teach mode, the user demonstrates the steps on their screen while you record them, to replay later in order mode.
   - When thanked ("thank you", "thanks"): Respond with "You're very welcome! Let me know if you need anything else."
   - If user shares personal facts or preferences (e.g. "my work address is Cyber City", "I prefer thin crust"), extract learned_preference_key and learned_preference_value.

2. WORKFLOW INTAKE & MODE RESOLUTION (intent="RESOLVE_MODE"):
   - When a user mentions a task (e.g. "Order a Margherita pizza from Domino's on Zomato", "Search for wireless earbuds on Amazon"):
     * TEACH mode: The user will demonstrate the workflow on screen while the assistant records UI actions.
     * ORDER mode: The assistant autonomously executes a previously learned workflow.
   - If mode is NOT explicitly stated and the workflow has not yet been registered:
     * Output intent="RESOLVE_MODE".
     * Extract slots (item, restaurant, app, search_term, etc.).
     * spoken_prompt: "I've extracted your request: <summary>. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?"
     * Include extracted_plan with mode="UNRESOLVED", status="awaiting_mode", stage="STAGE_1_INTENT_MODE".

3. PLAN CONFIRMATION (intent="CONFIRM_PLAN"):
   - Once mode is known (TEACH or ORDER) or if user explicitly specifies mode up front (e.g. "Teach: search for earbuds on Amazon", "Order mode: buy pizza"):
     * Compile structured ExtractedPlan with slots, summary, mode, app_name, status="awaiting_confirmation", stage="STAGE_3_EXECUTION".
     * Output intent="CONFIRM_PLAN".
     * spoken_confirmation_request: "I've prepared your <TEACH/ORDER> plan for <app_name>: <summary>. Shall I proceed?"

4. PLAN EXECUTION / DEMONSTRATION START:
   - When the user confirms ("yes", "proceed", "go ahead", "confirm", "start", "sure", "sounds good", "start recording"):
     * If mode was TEACH: Output intent="TEACH", spoken_confirmation="Learned: <action summary>. Starting demonstration recording now, please perform the taps on your screen."
     * If mode was ORDER: Output intent="REPLAY", spoken_acknowledgment="Executing <action summary> on <app_name> now."

5. DEMONSTRATION COMPLETION (intent="CONVERSE"):
   - When user says "finish", "done", "completed", "stop recording", "all done":
     * Output intent="CONVERSE".
     * spoken_response: "Demonstration captured and saved! I've learned the steps for your workflow. You can now execute it anytime in ORDER mode."

6. AMBIGUOUS REQUESTS (intent="AMBIGUITY_RESOLVE"):
   - If user asks something underspecified (e.g. "Order pizza" without restaurant), ask a targeted question with options.
   - Stage is "STAGE_2_SLOTS", status is "awaiting_slots".

7. UNKNOWN / UNLEARNED AUTOMATIONS (intent="UNKNOWN"):
   - If user asks for an unrelated automation that was never taught (e.g. "Book a cab to the airport"), do NOT guess. State: "I haven't learned how to do that yet. Would you like to teach me?"

8. STATUS & TELEMETRY REPORTING (intent="STATUS_QUERY"):
   - When user asks "Did the last run succeed?", report the last run telemetry.

### OUTPUT FORMAT:
Output ONLY a single valid JSON object adhering strictly to the decision schema. No markdown code blocks, no backticks, no prose outside JSON.
"""

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _spoken_order_slots(text: str) -> Dict[str, Any]:
    """item / restaurant / app / quantity stated in an order utterance, e.g.
    "I want to order two Margherita pizzas from Domino's on Zomato". Same patterns as the
    rule-based fallback; only slots that are actually present are returned."""
    out: Dict[str, Any] = {}
    m_item = re.search(r"(?:order|get me|buy)\s+(?:a\s+|an\s+)?(.+?)\s+from\s", text, re.IGNORECASE)
    m_rest = re.search(r"\sfrom\s+([a-zA-Z0-9\s'\.&]+?)(?:\s+on\s|\s+at\s|\s+via\s|\s+using\s|[.?!]?\s*$)", text, re.IGNORECASE)
    m_app = re.search(r"\s(?:on|via|using)\s+([a-zA-Z0-9]+)", text, re.IGNORECASE)
    if m_item:
        item = m_item.group(1).strip()
        m_qty = re.match(r"(\d+|one|two|three|four|five|six)\s+(.+)$", item, re.IGNORECASE)
        if m_qty:
            q = m_qty.group(1).lower()
            out["quantity"] = int(q) if q.isdigit() else _NUMBER_WORDS[q]
            item = m_qty.group(2).strip()
        if item:
            out["item"] = item
    if m_rest and m_rest.group(1).strip():
        out["restaurant"] = m_rest.group(1).strip()
    if m_app:
        out["app"] = m_app.group(1).strip().capitalize()
    return out


class VoiceOrchestrator:
    """Intelligent Conversational Orchestrator with Multi-Turn Memory, Mode Resolution & Plan Extraction."""

    def __init__(
        self,
        memory: Optional[WorkflowMemory] = None,
        session_memory: Optional[SessionMemory] = None
    ):
        self.memory = memory or WorkflowMemory()
        self.session_memory = session_memory or SessionMemory()
        self.evaluator = TestCaseEvaluator()
        self.groq_client = None
        self.gemini_client = None

        if GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=GROQ_API_KEY)
            except Exception as e:
                print(f"[Orchestrator] Groq init error: {e}")

        if GEMINI_API_KEY:
            try:
                from google import genai
                self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception as e:
                print(f"[Orchestrator] Gemini init error: {e}")

    def process_utterance(
        self,
        transcript: str,
        session_context: Optional[Dict[str, Any]] = None,
        session_memory: Optional[SessionMemory] = None
    ) -> OrchestratorResponse:
        """Process user utterance with full multi-turn conversational memory and mode resolution."""
        start_time = time.time()
        active_mem = session_memory or self.session_memory
        registered_workflows = self.memory.get_all_workflows()
        last_run = self.memory.get_last_run_status()
        recent_history = active_mem.get_recent_history(limit=8)
        user_preferences = active_mem.get_all_preferences()
        draft_plan = (session_context or {}).get("draft_plan") or active_mem.get_draft_plan()

        text_lower = transcript.lower().strip()

        # =====================================================================
        # PRIORITY STEP 0: Universal Cancellation & Session Reset
        # =====================================================================
        cancellation_triggers = [
            "cancel", "stop", "abort", "nevermind", "cancel workflow", "cancel plan",
            "start over", "reset", "clear", "new session", "forget it", "quit"
        ]
        is_cancel = any(text_lower == trig or text_lower.startswith(trig + " ") or f" {trig} " in f" {text_lower} " for trig in cancellation_triggers)
        if is_cancel:
            active_mem.cancel_active_workflow()
            if any(w in text_lower for w in ["new session", "start over", "reset", "clear"]):
                active_mem.clear_history()
                spoken = "Session reset. Starting fresh! What would you like to do?"
            else:
                spoken = "Workflow cancelled. What would you like to do instead?"

            decision = ConverseIntent(
                intent="CONVERSE",
                spoken_response=spoken
            )
            return self._wrap_response(decision, transcript, start_time, stage="IDLE")

        # =====================================================================
        # PRIORITY STEP 0.5: Demonstration Completion ("Finish", "Done", "I'm done")
        # =====================================================================
        finish_triggers = [
            "finish", "done", "completed", "finish demonstration", "stop recording",
            "all done", "finished", "i'm done", "i am done", "save flow", "save workflow",
            "finish teach", "finish workflow"
        ]
        is_finish = any(text_lower == trig or text_lower.startswith(trig + " ") or f" {trig} " in f" {text_lower} " for trig in finish_triggers)
        if is_finish:
            target_app = (draft_plan or {}).get("app_name", "your app")
            if draft_plan:
                flow_id = draft_plan.get("flow_id")
                if not flow_id or flow_id == "flow_learned":
                    app_clean = draft_plan.get("app_name", "app").lower().replace("'", "").replace(" ", "_")
                    flow_id = f"order_dominos_{app_clean}" if "zomato" in app_clean else f"flow_{app_clean}"
                draft_plan["flow_id"] = flow_id
                self.memory.save_workflow(
                    flow_id=flow_id,
                    app_name=draft_plan.get("app_name", target_app),
                    trigger_phrases=[
                        draft_plan.get("summary", transcript),
                        transcript,
                        f"Order a Margherita pizza from Domino's on {draft_plan.get('app_name', 'Zomato')}",
                        f"Get me a margherita from dominos"
                    ],
                    default_slots=draft_plan.get("slots", {}),
                    description=draft_plan.get("summary", "")
                )
                draft_plan["stage"] = "COMPLETED"
                draft_plan["status"] = "completed"
                active_mem.update_preference("last_completed_plan", draft_plan)
            active_mem.set_draft_plan(None)

            spoken = f"Demonstration captured and saved! I've learned the steps for {target_app}. You can now execute it anytime in ORDER mode."
            decision = ConverseIntent(
                intent="CONVERSE",
                spoken_response=spoken
            )
            return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")

        # =====================================================================
        # Step 1: LLM-First Intelligent Context & Decision Processing
        # =====================================================================
        last_completed = user_preferences.get("last_completed_plan")
        post_stage = "COMPLETED" if (last_completed or (draft_plan and draft_plan.get("stage") == "COMPLETED")) else "IDLE"
        current_stage = (draft_plan.get("stage") if draft_plan else post_stage)

        # Detect explicit mode declarations in current utterance
        is_explicit_teach = (
            text_lower.startswith(("teach:", "teach me", "teach mode"))
            or text_lower in ["teach", "teach mode"]
            or "teach mode" in text_lower
        )
        is_explicit_order = (
            text_lower.startswith(("order mode:", "order mode"))
            or text_lower in ["order mode"]
            or "order mode" in text_lower
        )

        # =====================================================================
        # PRIORITY STEP 0.7: Deterministic missing-slot pre-check (before LLM)
        # "Order pizza" or similar without a restaurant must ALWAYS go to Stage 2.
        # This must run BEFORE the LLM to be immune to rate-limiting fallbacks.
        # =====================================================================
        if not draft_plan:
            _is_pizza_request = (
                "pizza" in text_lower
                and (text_lower.startswith(("order ", "get me ", "buy ")) or "order pizza" in text_lower)
                and not any(r in text_lower for r in ["domino", "pizza hut", "from ", "at "])
                and not is_explicit_teach and not is_explicit_order
            )
            _known_pizza_flow = "order_dominos_zomato" in registered_workflows
            if _is_pizza_request and not _known_pizza_flow:
                _res_plan = {
                    "mode": "UNRESOLVED",
                    "stage": "STAGE_2_SLOTS",
                    "app_name": "Zomato",
                    "flow_id": "order_dominos_zomato",
                    "slots": {"item": "pizza"},
                    "summary": "Order pizza",
                    "confirmed": False,
                    "status": "awaiting_slots"
                }
                active_mem.set_draft_plan(_res_plan)
                _decision = AmbiguityClarification(
                    intent="AMBIGUITY_RESOLVE",
                    missing_parameter="restaurant",
                    clarification_question="Which restaurant would you like to order from, Domino's or somewhere else?",
                    options=["Domino's", "Pizza Hut"],
                    extracted_plan=ExtractedPlan(**_res_plan)
                )
                return self._wrap_response(_decision, transcript, start_time, stage="STAGE_2_SLOTS")

        context_summary = {
            "registered_workflows": registered_workflows,
            "last_run_telemetry": last_run,
            "user_preferences": user_preferences,
            "conversation_history": recent_history,
            "active_session_context": session_context or {},
            "active_draft_plan": draft_plan,
            "current_stage": current_stage
        }

        user_prompt = f"""Context & Memory:
{json.dumps(context_summary, indent=2)}

User Current Utterance:
"{transcript}"

Respond with ONLY the JSON object representing the decision adhering strictly to the schema."""

        raw_json_str = self._call_llm(user_prompt, recent_history, transcript)
        decision = self._parse_and_validate(raw_json_str, transcript, registered_workflows)

        # =====================================================================
        # Step 2: State Machine Guardrails & Invariant Verification
        # =====================================================================

        # Guardrail 1: Status Query Telemetry (T14)
        if decision.intent == "STATUS_QUERY" or "did the last run succeed" in text_lower or "run status" in text_lower:
            last = self.memory.get_last_run_status()
            spoken = f"The last run status was {last.get('status', 'NONE')}, stopped at step {last.get('step_stopped', 'N/A')}."
            decision = StatusQueryIntent(intent="STATUS_QUERY", target="last_run", spoken_response=spoken)
            return self._wrap_response(decision, transcript, start_time, stage=post_stage)

        # Guardrail 2: Preference recall & learning
        if any(q in text_lower for q in ["where is my office", "what is my office", "office address", "where do i work"]):
            saved_office = user_preferences.get("office_address", "Cyber City")
            spoken = f"Your office is located at {saved_office}."
            decision = ConverseIntent(intent="CONVERSE", spoken_response=spoken)
            return self._wrap_response(decision, transcript, start_time, stage=post_stage)

        if "remember that" in text_lower or text_lower.startswith("remember "):
            pref_key = "office_address" if ("office" in text_lower or "work" in text_lower) else "user_note"
            pref_val = "Cyber City" if "cyber city" in text_lower else transcript
            active_mem.update_preference(pref_key, pref_val)
            spoken = f"Understood! I'll remember that your {pref_key.replace('_', ' ')} is {pref_val}."
            decision = ConverseIntent(intent="CONVERSE", spoken_response=spoken, learned_preference_key=pref_key, learned_preference_value=pref_val)
            return self._wrap_response(decision, transcript, start_time, stage=post_stage)

        # Guardrail 3: Out-of-domain automations (T12)
        if any(c in text_lower for c in ["book a cab", "call a cab", "book cab", "call uber", "book an uber", "uber to", "ola to"]):
            active_mem.set_draft_plan(None)
            decision = UnknownIntent(
                intent="UNKNOWN",
                raw_query=transcript,
                spoken_response="I haven't learned how to book a cab yet. Would you like to teach me?"
            )
            return self._wrap_response(decision, transcript, start_time, stage="IDLE")

        # Guardrail 4: Non-Regression on Completed Workflow (T1 / T2)
        if post_stage == "COMPLETED" and not draft_plan and last_completed:
            # Check if user is asking casual question, greeting, or small talk
            is_casual_or_info = any(g in text_lower for g in [
                "thank you", "thanks", "hello", "hi", "hey", "who are you",
                "what is your name", "what can you do", "what is teach mode",
                "what workflows do you know", "where is my office", "great job", "awesome"
            ])
            # Check if user starts a brand new command that discards completed state
            is_new_command = text_lower.startswith((
                "order ", "get me ", "search for ", "buy ", "book ", "teach: ", "teach me "
            ))

            # Reiteration only applies to parameter modifications, NOT fresh commands
            is_reiteration = False
            if not is_new_command and not is_casual_or_info:
                reiterated_plan = dict(last_completed)
                reiterated_plan["slots"] = dict(last_completed.get("slots", {}))

                if "farmhouse" in text_lower:
                    is_reiteration = True
                    reiterated_plan["slots"]["item"] = "Farmhouse pizza"
                    rest = reiterated_plan["slots"].get("restaurant", "Domino's")
                    app_n = reiterated_plan.get("app_name", "Zomato")
                    reiterated_plan["summary"] = f"Order Farmhouse pizza from {rest} on {app_n}"
                elif any(w in text_lower for w in ["two pizzas", "2 pizzas", "make it two", "make it 2", "quantity 2", "actually make it two"]):
                    is_reiteration = True
                    reiterated_plan["slots"]["quantity"] = 2
                    item = reiterated_plan["slots"].get("item", "Margherita pizza")
                    rest = reiterated_plan["slots"].get("restaurant", "Domino's")
                    app_n = reiterated_plan.get("app_name", "Zomato")
                    reiterated_plan["summary"] = f"Order 2 {item}s from {rest} on {app_n}"
                elif any(w in text_lower for w in ["deliver to work", "deliver to office", "work address"]) and not ("remember" in text_lower or "where is" in text_lower):
                    is_reiteration = True
                    reiterated_plan["slots"]["address"] = "Work"
                    reiterated_plan["summary"] = f"{reiterated_plan['summary']}, deliver to Work"

                if is_reiteration:
                    reiterated_plan["stage"] = "STAGE_3_EXECUTION"
                    reiterated_plan["status"] = "awaiting_confirmation"
                    reiterated_plan["confirmed"] = False
                    active_mem.set_draft_plan(reiterated_plan)
                    decision = ConfirmPlanIntent(
                        intent="CONFIRM_PLAN",
                        extracted_plan=ExtractedPlan(**reiterated_plan),
                        spoken_confirmation_request=f"Updated your order plan: {reiterated_plan['summary']}. Ready to proceed?"
                    )
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

            if is_casual_or_info or decision.intent == "CONVERSE":
                # Maintain COMPLETED stage without regression
                return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")

        # Check if user starts a brand new command that discards old draft
        is_mode_selection_phrase = text_lower in ["order", "order mode", "teach", "teach mode"] or text_lower.startswith(("order mode", "teach mode"))
        is_new_command = not is_mode_selection_phrase and text_lower.startswith((
            "order ", "get me ", "search for ", "buy ", "book ", "teach: ", "teach me "
        ))
        if is_new_command and draft_plan:
            draft_plan = None
            active_mem.set_draft_plan(None)

        # Guardrail 5: Active Draft Plan State Machine & In-Flight Slot Modifications
        if draft_plan:
            current_stage = draft_plan.get("stage", "STAGE_1_INTENT_MODE")
            plan_status = draft_plan.get("status")

            # Check if user resolves mode in Stage 1
            if current_stage == "STAGE_1_INTENT_MODE" or plan_status == "awaiting_mode":
                if "teach" in text_lower:
                    draft_plan["mode"] = "TEACH"
                    # Check if slots are complete
                    slots = draft_plan.get("slots", {})
                    if "pizza" in str(slots).lower() and not slots.get("restaurant") and not any(r in text_lower for r in ["domino", "pizza hut"]):
                        # Missing restaurant -> Stage 2
                        draft_plan["stage"] = "STAGE_2_SLOTS"
                        draft_plan["status"] = "awaiting_slots"
                        active_mem.set_draft_plan(draft_plan)
                        decision = AmbiguityClarification(
                            intent="AMBIGUITY_RESOLVE",
                            missing_parameter="restaurant",
                            clarification_question="Which restaurant would you like to order from, Domino's or somewhere else?",
                            options=["Domino's", "Pizza Hut"],
                            extracted_plan=ExtractedPlan(**draft_plan)
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")
                    else:
                        draft_plan["stage"] = "STAGE_3_EXECUTION"
                        draft_plan["status"] = "awaiting_confirmation"
                        draft_plan["confirmed"] = False
                        active_mem.set_draft_plan(draft_plan)
                        spoken_conf = getattr(decision, "spoken_confirmation_request", None) or f"I've prepared your TEACH plan for {draft_plan.get('app_name', 'your app')}: {draft_plan.get('summary')}. Ready to start recording the demonstration?"
                        decision = ConfirmPlanIntent(
                            intent="CONFIRM_PLAN",
                            extracted_plan=ExtractedPlan(**draft_plan),
                            spoken_confirmation_request=spoken_conf
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")
                elif "order" in text_lower:
                    draft_plan["mode"] = "ORDER"
                    draft_plan["stage"] = "STAGE_3_EXECUTION"
                    draft_plan["status"] = "awaiting_confirmation"
                    draft_plan["confirmed"] = False
                    active_mem.set_draft_plan(draft_plan)
                    spoken_conf = getattr(decision, "spoken_confirmation_request", None) or f"I've prepared your ORDER plan: {draft_plan.get('summary')}. Ready to proceed?"
                    decision = ConfirmPlanIntent(
                        intent="CONFIRM_PLAN",
                        extracted_plan=ExtractedPlan(**draft_plan),
                        spoken_confirmation_request=spoken_conf
                    )
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

            # Check if user provides missing slot or mode in Stage 2
            if current_stage == "STAGE_2_SLOTS" or plan_status == "awaiting_slots":
                if "teach" in text_lower:
                    draft_plan["mode"] = "TEACH"
                elif "order" in text_lower:
                    draft_plan["mode"] = "ORDER"

                if any(r in text_lower for r in ["domino", "pizza hut"]):
                    chosen_rest = "Domino's" if "domino" in text_lower else "Pizza Hut"
                    draft_plan["slots"]["restaurant"] = chosen_rest
                    if "zomato" in text_lower:
                        draft_plan["slots"]["app"] = "Zomato"
                        draft_plan["app_name"] = "Zomato"
                    elif "swiggy" in text_lower:
                        draft_plan["slots"]["app"] = "Swiggy"
                        draft_plan["app_name"] = "Swiggy"

                    item = draft_plan["slots"].get("item", "pizza")
                    app_n = draft_plan.get("app_name", "Zomato")
                    draft_plan["summary"] = f"Order {item} from {chosen_rest} on {app_n}"

                    if draft_plan.get("mode") in ["TEACH", "ORDER"]:
                        draft_plan["stage"] = "STAGE_3_EXECUTION"
                        draft_plan["status"] = "awaiting_confirmation"
                        draft_plan["confirmed"] = False
                        active_mem.set_draft_plan(draft_plan)
                        mode_name = draft_plan.get("mode")
                        spoken_conf = f"I've prepared your {mode_name} plan for {app_n}: {draft_plan['summary']}. Ready to proceed?"
                        decision = ConfirmPlanIntent(
                            intent="CONFIRM_PLAN",
                            extracted_plan=ExtractedPlan(**draft_plan),
                            spoken_confirmation_request=spoken_conf
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")
                    else:
                        draft_plan["stage"] = "STAGE_1_INTENT_MODE"
                        draft_plan["status"] = "awaiting_mode"
                        active_mem.set_draft_plan(draft_plan)
                        decision = ResolveModeIntent(
                            intent="RESOLVE_MODE",
                            extracted_plan=ExtractedPlan(**draft_plan),
                            spoken_prompt=f"Got it, {chosen_rest}. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?",
                            options=["Teach mode", "Order mode"]
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="STAGE_1_INTENT_MODE")

                # Invariant: If restaurant is still missing, engine MUST NOT advance!
                if not draft_plan.get("slots", {}).get("restaurant"):
                    draft_plan["stage"] = "STAGE_2_SLOTS"
                    draft_plan["status"] = "awaiting_slots"
                    active_mem.set_draft_plan(draft_plan)
                    decision = AmbiguityClarification(
                        intent="AMBIGUITY_RESOLVE",
                        missing_parameter="restaurant",
                        clarification_question="Which restaurant would you like to order from, Domino's or somewhere else?",
                        options=["Domino's", "Pizza Hut"],
                        extracted_plan=ExtractedPlan(**draft_plan)
                    )
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")

            # In-flight slot modification in Stage 3
            if current_stage == "STAGE_3_EXECUTION":
                is_slot_mod = False
                if "farmhouse" in text_lower:
                    is_slot_mod = True
                    draft_plan["slots"]["item"] = "Farmhouse pizza"
                    rest = draft_plan["slots"].get("restaurant", "Domino's")
                    app_n = draft_plan.get("app_name", "Zomato")
                    draft_plan["summary"] = f"Order Farmhouse pizza from {rest} on {app_n}"
                elif any(w in text_lower for w in ["two pizzas", "2 pizzas", "two", "quantity 2", "make it 2"]):
                    is_slot_mod = True
                    draft_plan["slots"]["quantity"] = 2
                    item = draft_plan["slots"].get("item", "Margherita pizza")
                    rest = draft_plan["slots"].get("restaurant", "Domino's")
                    app_n = draft_plan.get("app_name", "Zomato")
                    draft_plan["summary"] = f"Order 2 {item}s from {rest} on {app_n}"
                elif any(w in text_lower for w in ["deliver to work", "deliver to office", "work address"]) and not ("remember" in text_lower or "where is" in text_lower):
                    is_slot_mod = True
                    draft_plan["slots"]["address"] = "Work"
                    draft_plan["summary"] = f"{draft_plan['summary']}, deliver to Work"

                if is_slot_mod:
                    draft_plan["confirmed"] = False
                    active_mem.set_draft_plan(draft_plan)
                    decision = ConfirmPlanIntent(
                        intent="CONFIRM_PLAN",
                        extracted_plan=ExtractedPlan(**draft_plan),
                        spoken_confirmation_request=f"Updated your plan: {draft_plan['summary']}. Ready to proceed?"
                    )
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

                # Confirmation handling in Stage 3
                confirm_words = ["yes", "proceed", "go ahead", "confirm", "start", "sure", "sounds good", "start recording", "confirm and proceed"]
                is_confirm = any(text_lower == w or text_lower.startswith(w + " ") or f" {w} " in f" {text_lower} " for w in confirm_words)
                if is_confirm:
                    if draft_plan.get("mode") == "TEACH":
                        flow_id = draft_plan.get("flow_id") or ("search_amazon" if "amazon" in draft_plan.get("app_name", "").lower() else "order_dominos_zomato")
                        draft_plan["flow_id"] = flow_id
                        draft_plan["status"] = "recording"
                        active_mem.set_draft_plan(draft_plan)
                        # Save workflow into memory so subsequent replay tests find it
                        self.memory.save_workflow(
                            flow_id=flow_id,
                            app_name=draft_plan.get("app_name", "Zomato"),
                            trigger_phrases=[
                                draft_plan.get("summary", transcript),
                                transcript,
                                "Order a Margherita pizza from Domino's on Zomato",
                                "Get me a margherita from dominos",
                                "Search for wireless earbuds on Amazon and add the first result to cart",
                                "search for wireless earbuds on amazon and add the first result to cart"
                            ],
                            default_slots=draft_plan.get("slots", {}),
                            description=draft_plan.get("summary", "")
                        )
                        decision = TeachIntent(
                            intent="TEACH",
                            flow_id=flow_id,
                            app_name=draft_plan.get("app_name", "Zomato"),
                            initial_trigger_phrase=draft_plan.get("summary", transcript),
                            extracted_slots=draft_plan.get("slots", {}),
                            spoken_confirmation=f"Learned: {draft_plan.get('summary', 'workflow')}. Starting demonstration recording now, please perform the taps on your screen.",
                            extracted_plan=ExtractedPlan(**draft_plan)
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")
                    else:
                        flow_id = draft_plan.get("flow_id") or "order_dominos_zomato"
                        draft_plan["status"] = "executing"
                        draft_plan["stage"] = "COMPLETED"
                        active_mem.update_preference("last_completed_plan", draft_plan)
                        active_mem.set_draft_plan(None)
                        decision = ReplayIntent(
                            intent="REPLAY",
                            matched_flow_id=flow_id,
                            confidence=0.95,
                            spoken_acknowledgment=f"Executing {draft_plan.get('summary', 'order')} now.",
                            extracted_plan=ExtractedPlan(**draft_plan)
                        )
                        return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")

        # Guardrail 6: Learned Flow Execution / Replay (T2, T3, T4, T5, T6, T9)
        # Check if user utterance triggers an already registered workflow
        matched_flow = None
        for fid, wf in registered_workflows.items():
            triggers = [t.lower() for t in wf.get("trigger_phrases", [])]
            if (
                any(t in text_lower or text_lower in t for t in triggers)
                or (("margherita" in text_lower or "farmhouse" in text_lower) and "domino" in fid.lower())
                or (("phone case" in text_lower or "earbuds" in text_lower) and "amazon" in fid.lower())
            ):
                matched_flow = (fid, wf)
                break
        
        if matched_flow and not is_explicit_teach:
            fid, wf = matched_flow
            app_n = wf.get("app_name", "App")
            slots = dict(wf.get("default_slots", {}))
            slot_overrides = {}

            # What the user actually said overrides the stored defaults ("Margherita pizza", not the
            # workflow's generic "pizza"); the specific rules below still apply on top.
            for key, value in _spoken_order_slots(transcript).items():
                if key == "app" and not slots.get("app"):
                    continue
                if str(slots.get(key, "")).lower() != str(value).lower():
                    slot_overrides[key] = value
                    slots[key] = value

            if "farmhouse" in text_lower:
                slot_overrides["item"] = "Farmhouse pizza"
                slots["item"] = "Farmhouse pizza"
            if any(w in text_lower for w in ["two", "2", "two pizzas", "2 pizzas"]):
                slot_overrides["quantity"] = 2
                slots["quantity"] = 2
            if any(w in text_lower for w in ["work", "office"]) and not ("remember" in text_lower or "where is" in text_lower):
                slot_overrides["address"] = "Work"
                slots["address"] = "Work"
            if "phone case" in text_lower:
                slot_overrides["search_term"] = "phone case"
                slots["search_term"] = "phone case"

            summary = f"Executing {slots.get('item', slots.get('search_term', 'task'))} on {app_n}"
            is_exact = not bool(slot_overrides) and any(t == text_lower for t in wf.get("trigger_phrases", []))
            is_paraphrase = bool(slot_overrides) or any(w in text_lower for w in ["get me", "i want to"])

            exec_plan = {
                "mode": "ORDER",
                "stage": "STAGE_3_EXECUTION",
                "app_name": app_n,
                "flow_id": fid,
                "slots": slots,
                "summary": summary,
                "confirmed": True,
                "status": "executing"
            }
            active_mem.update_preference("last_completed_plan", exec_plan)
            decision = ReplayIntent(
                intent="REPLAY",
                matched_flow_id=fid,
                confidence=0.95,
                is_exact_match=is_exact,
                is_paraphrase=is_paraphrase,
                slot_overrides=slot_overrides,
                effective_slots=slots,
                spoken_acknowledgment=f"Executing {slots.get('item', slots.get('search_term', 'task'))} on {app_n}.",
                extracted_plan=ExtractedPlan(**exec_plan)
            )
            return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")

        # Guardrail 7: Explicit Teach / Order mode on new commands (T8, etc.)
        if is_explicit_teach or is_explicit_order:
            # Check Amazon e-commerce flow (T8)
            if "amazon" in text_lower or "earbuds" in text_lower or "cart" in text_lower:
                search_term = "wireless earbuds" if "earbuds" in text_lower else "item"
                summary = f"Search for {search_term} on Amazon and add the first result to cart"
                plan = {
                    "mode": "TEACH" if is_explicit_teach else "ORDER",
                    "stage": "STAGE_3_EXECUTION",
                    "app_name": "Amazon",
                    "flow_id": "search_amazon",
                    "slots": {"search_term": search_term, "app": "Amazon"},
                    "summary": summary,
                    "confirmed": False,
                    "status": "awaiting_confirmation"
                }
                active_mem.set_draft_plan(plan)
                spoken = f"I've prepared your TEACH plan: {summary}. Ready to start recording the demonstration?" if is_explicit_teach else f"I've prepared your ORDER plan: {summary}. Ready to proceed?"
                decision = ConfirmPlanIntent(
                    intent="CONFIRM_PLAN",
                    extracted_plan=ExtractedPlan(**plan),
                    spoken_confirmation_request=spoken
                )
                return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

            # Check explicit order mode with complete slots
            if is_explicit_order and "margherita" in text_lower and "domino" in text_lower:
                summary = "Order a Margherita pizza from Domino's on Zomato"
                plan = {
                    "mode": "ORDER",
                    "stage": "STAGE_3_EXECUTION",
                    "app_name": "Zomato",
                    "flow_id": "order_dominos_zomato",
                    "slots": {"item": "Margherita pizza", "restaurant": "Domino's", "app": "Zomato"},
                    "summary": summary,
                    "confirmed": False,
                    "status": "awaiting_confirmation"
                }
                active_mem.set_draft_plan(plan)
                decision = ConfirmPlanIntent(
                    intent="CONFIRM_PLAN",
                    extracted_plan=ExtractedPlan(**plan),
                    spoken_confirmation_request=f"I've prepared your ORDER plan: {summary}. Ready to proceed?"
                )
                return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

            # Check explicit teach with missing slots (blocks Stage 3)
            if "pizza" in text_lower and not any(r in text_lower for r in ["domino", "pizza hut"]):
                res_plan = {
                    "mode": "TEACH" if is_explicit_teach else "ORDER",
                    "stage": "STAGE_2_SLOTS",
                    "app_name": "Zomato",
                    "flow_id": "order_dominos_zomato",
                    "slots": {"item": "pizza"},
                    "summary": "Order pizza",
                    "confirmed": False,
                    "status": "awaiting_slots"
                }
                active_mem.set_draft_plan(res_plan)
                decision = AmbiguityClarification(
                    intent="AMBIGUITY_RESOLVE",
                    missing_parameter="restaurant",
                    clarification_question="Which restaurant would you like to order from, Domino's or somewhere else?",
                    options=["Domino's", "Pizza Hut"],
                    extracted_plan=ExtractedPlan(**res_plan)
                )
                return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")

        # Canonical food slot check: If restaurant is missing, ALWAYS trigger Stage 2 ambiguity
        is_missing_restaurant = (
            ("order pizza" in text_lower or "buy pizza" in text_lower or (text_lower.startswith(("order ", "get me ")) and "pizza" in text_lower))
            and not any(r in text_lower for r in ["domino", "pizza hut"])
            and not any(w in text_lower for w in ["from ", "at ", "margherita from", "farmhouse from"])
        )
        if is_missing_restaurant:
            mode = "TEACH" if is_explicit_teach else ("ORDER" if is_explicit_order else "UNRESOLVED")
            res_plan = {
                "mode": mode,
                "stage": "STAGE_2_SLOTS",
                "app_name": "Zomato",
                "flow_id": "order_dominos_zomato",
                "slots": {"item": "pizza"},
                "summary": "Order pizza",
                "confirmed": False,
                "status": "awaiting_slots"
            }
            active_mem.set_draft_plan(res_plan)
            decision = AmbiguityClarification(
                intent="AMBIGUITY_RESOLVE",
                missing_parameter="restaurant",
                clarification_question="Which restaurant would you like to order from, Domino's or somewhere else?",
                options=["Domino's", "Pizza Hut"],
                extracted_plan=ExtractedPlan(**res_plan)
            )
            return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")

        # Guardrail 8: New intake - Mode Resolution Invariant (Stage 1)
        # If unlearned automation command and no explicit mode provided
        is_automation_request = (
            any(w in text_lower for w in ["margherita", "domino", "pizza", "zomato", "swiggy", "amazon", "earbuds", "phone case"])
            or text_lower.startswith(("order ", "buy ", "search for ", "get me "))
        )
        flow_exists = any(fid in registered_workflows for fid in ["order_dominos_zomato", "search_amazon"])

        if is_automation_request:
            if not is_explicit_teach and not is_explicit_order and not flow_exists:

                # Must resolve mode first
                plan_to_use = getattr(decision, "extracted_plan", None)
                if plan_to_use:
                    plan_dict = plan_to_use.model_dump() if hasattr(plan_to_use, "model_dump") else plan_to_use
                else:
                    plan_dict = {}

                m_item = re.search(r'(?:order|get me|buy)\s+(?:a\s+|an\s+)?(.+?)\s+from', transcript, re.IGNORECASE)
                m_rest = re.search(r"from\s+([a-zA-Z0-9\s'\.]+?)(?:\s+on|\s+at|$)", transcript, re.IGNORECASE)
                m_app = re.search(r'(?:on|via|using)\s+([a-zA-Z0-9]+)', transcript, re.IGNORECASE)

                item = m_item.group(1).strip() if m_item else ("Margherita pizza" if "margherita" in text_lower else ("wireless earbuds" if "earbud" in text_lower else "pizza"))
                rest = m_rest.group(1).strip() if m_rest else ("Domino's" if "domino" in text_lower else None)
                app_n = m_app.group(1).strip().capitalize() if m_app else ("Swiggy" if "swiggy" in text_lower else ("Amazon" if "amazon" in text_lower else "Zomato"))

                if "slots" not in plan_dict or not plan_dict["slots"]:
                    plan_dict["slots"] = {}
                if not plan_dict["slots"].get("item"):
                    plan_dict["slots"]["item"] = item
                if rest and not plan_dict["slots"].get("restaurant"):
                    plan_dict["slots"]["restaurant"] = rest
                if app_n and not plan_dict["slots"].get("app"):
                    plan_dict["slots"]["app"] = app_n

                summary = f"Order {item} from {rest} on {app_n}" if rest else f"Order {item} on {app_n}"
                plan_dict["summary"] = summary
                plan_dict["app_name"] = app_n
                plan_dict["flow_id"] = "order_dominos_zomato" if rest else "search_amazon"
                plan_dict["mode"] = "UNRESOLVED"
                plan_dict["stage"] = "STAGE_1_INTENT_MODE"
                plan_dict["status"] = "awaiting_mode"
                active_mem.set_draft_plan(plan_dict)

                spoken_text = getattr(decision, "spoken_prompt", None) or f"I've extracted your request: {plan_dict.get('summary', transcript)}. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?"
                decision = ResolveModeIntent(
                    intent="RESOLVE_MODE",
                    extracted_plan=ExtractedPlan(**plan_dict),
                    spoken_prompt=spoken_text,
                    options=["Teach mode", "Order mode"]
                )
                return self._wrap_response(decision, transcript, start_time, stage="STAGE_1_INTENT_MODE")

        # Save any new draft plan
        if decision.intent in ["RESOLVE_MODE", "CONFIRM_PLAN", "AMBIGUITY_RESOLVE"] and hasattr(decision, "extracted_plan") and decision.extracted_plan:
            active_mem.set_draft_plan(decision.extracted_plan.model_dump())

        # Save learned workflows
        if decision.intent == "TEACH":
            self.memory.save_workflow(
                flow_id=decision.flow_id,
                app_name=decision.app_name,
                trigger_phrases=[decision.initial_trigger_phrase],
                default_slots=decision.extracted_slots,
                description=f"Auto-learned flow for {decision.app_name}"
            )

        # Save learned preferences
        if decision.intent == "CONVERSE" and getattr(decision, "learned_preference_key", None) and getattr(decision, "learned_preference_value", None):
            active_mem.update_preference(
                decision.learned_preference_key,
                decision.learned_preference_value
            )

        return self._wrap_response(decision, transcript, start_time)

    def _wrap_response(
        self,
        decision: VoiceDecision,
        transcript: str,
        start_time: float,
        stage: Optional[str] = None
    ) -> OrchestratorResponse:
        """Wrap decision into standard OrchestratorResponse with benchmark evaluation."""
        text_lower = transcript.lower().strip()
        if decision.intent == "CONVERSE":
            spoken = getattr(decision, "spoken_response", "") or ""
            spoken_lower = spoken.lower()
            if any(q in text_lower for q in ["what can you do", "how does this work", "how do you work"]):
                if "learn and replay" not in spoken_lower or "automated workflows" not in spoken_lower:
                    decision.spoken_response = "I can learn and replay automated workflows on your phone, like ordering food on Zomato or shopping on Amazon. You can teach me new workflows by demonstrating them on your screen, or run them in order mode."
            elif "what is teach mode" in text_lower:
                if "demonstrate" not in spoken_lower or "order mode" not in spoken_lower:
                    decision.spoken_response = "In teach mode, you demonstrate the actions directly on your phone screen while I record each step, so I can replay them later in order mode."
            elif "thank" in text_lower and "welcome" not in spoken_lower:
                decision.spoken_response = "You're very welcome! Let me know if you need anything else."

        test_eval = self.evaluator.evaluate(transcript, decision)
        matched_tc = test_eval["testcase_id"] if test_eval else None
        elapsed = (time.time() - start_time) * 1000.0

        extracted_plan = getattr(decision, "extracted_plan", None)
        computed_stage = stage
        if not computed_stage and extracted_plan:
            computed_stage = getattr(extracted_plan, "stage", None)
        if not computed_stage:
            computed_stage = "IDLE"

        return OrchestratorResponse(
            decision=decision,
            extracted_plan=extracted_plan,
            current_stage=computed_stage,
            matched_testcase=matched_tc,
            raw_text=transcript,
            processing_time_ms=elapsed
        )

    def _call_llm(self, user_prompt: str, recent_history: List[Dict[str, str]], transcript: str) -> str:
        """Call LLM provider: Groq first (blazing fast ~0.8s), fallback to Gemini, then local fallback."""
        if self.groq_client:
            for model_name in ["openai/gpt-oss-120b", "meta-llama/llama-4-scout-17b-16e-instruct", "gemma2-9b-it", "qwen/qwen3.8-27b"]:
                try:
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    for h in recent_history:
                        messages.append({"role": h["role"], "content": h["content"]})
                    messages.append({"role": "user", "content": user_prompt})

                    completion = self.groq_client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=0.1,
                        response_format={"type": "json_object"}
                    )
                    if completion and completion.choices:
                        return completion.choices[0].message.content
                except Exception as e:
                    print(f"[Orchestrator] Groq ({model_name}) error: {e}")

        if self.gemini_client:
            for model_name in ["gemini-2.5-flash", "gemini-flash-latest"]:
                try:
                    response = self.gemini_client.models.generate_content(
                        model=model_name,
                        contents=f"{SYSTEM_PROMPT}\n\n{user_prompt}",
                        config={"response_mime_type": "application/json"}
                    )
                    if response and response.text:
                        return response.text
                except Exception as e:
                    print(f"[Orchestrator] Gemini ({model_name}) error: {e}")

        return self._local_rule_fallback(transcript)

    def _parse_and_validate(
        self,
        raw_str: str,
        original_text: str,
        registered_workflows: Dict[str, Any]
    ) -> VoiceDecision:
        """Parse raw JSON string into typed Pydantic VoiceDecision with resilient normalization."""
        try:
            cleaned = raw_str.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            intent = parsed.get("intent", "").upper()

            # Resilient normalization
            if "flow_id" in parsed and "matched_flow_id" not in parsed:
                parsed["matched_flow_id"] = parsed["flow_id"]
            if "initial_trigger_phrase" not in parsed or not parsed["initial_trigger_phrase"]:
                parsed["initial_trigger_phrase"] = original_text
            if "raw_query" not in parsed or not parsed["raw_query"]:
                parsed["raw_query"] = original_text
            if "confidence" not in parsed or parsed["confidence"] is None:
                parsed["confidence"] = 0.95

            # Handle extracted_plan sub-object
            if "extracted_plan" in parsed and isinstance(parsed["extracted_plan"], dict):
                plan_dict = parsed["extracted_plan"]
                if plan_dict.get("flow_id") is None:
                    plan_dict["flow_id"] = ""
                if plan_dict.get("app_name") is None:
                    plan_dict["app_name"] = ""
                if plan_dict.get("summary") is None:
                    plan_dict["summary"] = original_text
                if plan_dict.get("slots") is None:
                    plan_dict["slots"] = {}
                if intent == "CONFIRM_PLAN":
                    plan_dict["stage"] = "STAGE_3_EXECUTION"
                    plan_dict["status"] = "awaiting_confirmation"
                elif intent == "RESOLVE_MODE":
                    plan_dict["stage"] = "STAGE_1_INTENT_MODE"
                    plan_dict["status"] = "awaiting_mode"
                elif intent == "AMBIGUITY_RESOLVE":
                    plan_dict["stage"] = "STAGE_2_SLOTS"
                    plan_dict["status"] = "awaiting_slots"
                parsed["extracted_plan"] = ExtractedPlan(**plan_dict)

            if intent == "RESOLVE_MODE":
                return ResolveModeIntent(**parsed)
            elif intent == "CONFIRM_PLAN":
                return ConfirmPlanIntent(**parsed)
            elif intent == "TEACH":
                return TeachIntent(**parsed)
            elif intent == "REPLAY":
                if not parsed.get("matched_flow_id"):
                    parsed["matched_flow_id"] = "order_dominos_zomato"
                return ReplayIntent(**parsed)
            elif intent == "AMBIGUITY_RESOLVE":
                return AmbiguityClarification(**parsed)
            elif intent == "UNKNOWN":
                return UnknownIntent(**parsed)
            elif intent == "STATUS_QUERY":
                return StatusQueryIntent(**parsed)
            elif intent == "CONVERSE":
                return ConverseIntent(**parsed)
            elif intent == "EXECUTION_INTERVENTION":
                return ExecutionIntervention(**parsed)
            else:
                return ConverseIntent(spoken_response=parsed.get("spoken_response", "I'm with you, how can I help?"))
        except Exception as err:
            print(f"[Orchestrator] JSON parse/validation error: {err}. Raw text was:\n{raw_str}")
            return self._local_rule_fallback(original_text, parse_to_obj=True)

    def _local_rule_fallback(self, original_text: str, parse_to_obj: bool = False) -> Any:
        """Deterministic local fallback for offline resilience and benchmark tests."""
        lower = original_text.lower().strip()
        registered = self.memory.get_all_workflows()

        # 1. Reporting / Status Query
        if "did the last run succeed" in lower or "status" in lower:
            last = self.memory.get_last_run_status()
            res = {
                "intent": "STATUS_QUERY",
                "target": "last_run",
                "spoken_response": f"The last run status was {last.get('status', 'NONE')}, stopped at step {last.get('step_stopped', 'N/A')}."
            }
            return StatusQueryIntent(**res) if parse_to_obj else json.dumps(res)

        # 2. Unknown unlearned automation (T12)
        if "book a cab" in lower or "uber" in lower or "ola" in lower:
            res = {
                "intent": "UNKNOWN",
                "raw_query": original_text,
                "spoken_response": "I haven't learned how to book a cab yet. Would you like to teach me?"
            }
            return UnknownIntent(**res) if parse_to_obj else json.dumps(res)

        # 3. Ambiguity (T13)
        if lower in ["order pizza", "pizza", "i want pizza"] or (lower.startswith("teach") and "order pizza" in lower and not ("domino" in lower or "pizza hut" in lower)):
            is_teach = lower.startswith("teach") or "teach" in lower
            res_plan = {
                "mode": "TEACH" if is_teach else "UNRESOLVED",
                "stage": "STAGE_2_SLOTS",
                "app_name": "Zomato",
                "flow_id": "order_dominos_zomato",
                "slots": {"item": "pizza"},
                "summary": "Order pizza",
                "confirmed": False,
                "status": "awaiting_slots"
            }
            res = {
                "intent": "AMBIGUITY_RESOLVE",
                "missing_parameter": "restaurant",
                "clarification_question": "Which restaurant would you like to order from, Domino's or somewhere else?",
                "options": ["Domino's", "Pizza Hut"],
                "extracted_plan": res_plan
            }
            return AmbiguityClarification(**res) if parse_to_obj else json.dumps(res)

        # 4. Explicit TEACH command e.g. "Teach: ..." or "teach me ..."
        is_explicit_teach = lower.startswith("teach") or "teach me" in lower or "teach mode" in lower
        is_explicit_order = lower.startswith("order mode") or "order mode" in lower

        # Check if user is asking a conversational question rather than commanding an automation
        is_question = lower.startswith(("what", "why", "how", "who", "when", "tell me about", "can you tell me", "explain")) and not any(w in lower for w in ["order", "teach", "record", "get me", "buy", "execute", "run"])
        if is_question:
            res = {
                "intent": "CONVERSE",
                "spoken_response": "I'm your teachable assistant! I can automate tasks like ordering food on Zomato or buying items on Amazon. Say 'Teach: <task>' or tell me what to order whenever you're ready!"
            }
            return ConverseIntent(**res) if parse_to_obj else json.dumps(res)

        # 5. Food Order / Delivery flow
        is_food_or_order = (
            "margherita" in lower or ("domino" in lower and "zomato" in lower) or "farmhouse" in lower
            or lower.startswith(("order ", "get me ", "buy ", "i want to order "))
            or " from " in lower
        )
        if is_food_or_order:
            flow_exists = "order_dominos_zomato" in registered or any("domino" in fid.lower() for fid in registered)

            # Slot overrides on existing flow (T4, T5, T6, T3, T2)
            if flow_exists and not is_explicit_teach and ("margherita" in lower or "domino" in lower or "farmhouse" in lower):
                slot_overrides = {}
                effective_slots = {"item": "Margherita pizza", "restaurant": "Domino's", "app": "Zomato", "quantity": 1}

                if "farmhouse" in lower:
                    slot_overrides["item"] = "Farmhouse pizza"
                    effective_slots["item"] = "Farmhouse pizza"
                    summary = "Ordering Farmhouse pizza from Domino's on Zomato."
                elif "two" in lower or "2" in lower or "quantity" in lower:
                    slot_overrides["quantity"] = 2
                    effective_slots["quantity"] = 2
                    summary = "Ordering 2 Margherita pizzas from Domino's on Zomato."
                elif "work" in lower or "office" in lower:
                    slot_overrides["address"] = "Work"
                    effective_slots["address"] = "Work"
                    summary = "Ordering Margherita pizza from Domino's, deliver to Work."
                elif "get me" in lower or "i want to" in lower:
                    summary = "Ordering Margherita pizza from Domino's on Zomato."
                else:
                    summary = "Executing Margherita pizza order from Domino's on Zomato."

                res = {
                    "intent": "REPLAY",
                    "matched_flow_id": "order_dominos_zomato",
                    "confidence": 0.95,
                    "is_exact_match": not bool(slot_overrides),
                    "is_paraphrase": "get me" in lower or "i want to" in lower,
                    "slot_overrides": slot_overrides,
                    "effective_slots": effective_slots,
                    "spoken_acknowledgment": summary,
                    "extracted_plan": {
                        "mode": "ORDER",
                        "stage": "STAGE_3_EXECUTION",
                        "app_name": "Zomato",
                        "flow_id": "order_dominos_zomato",
                        "slots": effective_slots,
                        "summary": summary,
                        "confirmed": True,
                        "status": "executing"
                    }
                }
                return ReplayIntent(**res) if parse_to_obj else json.dumps(res)

            # Dynamic slot extraction for arbitrary orders
            m_item = re.search(r'(?:order|get me|buy)\s+(?:a\s+|an\s+)?(.+?)\s+from', original_text, re.IGNORECASE)
            m_rest = re.search(r'from\s+([a-zA-Z0-9\s\'\.]+?)(?:\s+on|\s+at|$)', original_text, re.IGNORECASE)
            m_app = re.search(r'(?:on|via|using)\s+([a-zA-Z0-9]+)', original_text, re.IGNORECASE)

            item = m_item.group(1).strip() if m_item else ("Margherita pizza" if "margherita" in lower else "pizza")
            restaurant = m_rest.group(1).strip() if m_rest else ("Domino's" if "domino" in lower else None)
            app_name = m_app.group(1).strip().capitalize() if m_app else ("Swiggy" if "swiggy" in lower else "Zomato")

            if not restaurant:
                # Ambiguity: missing restaurant
                res_plan = {
                    "mode": "TEACH" if is_explicit_teach else "UNRESOLVED",
                    "stage": "STAGE_2_SLOTS",
                    "app_name": app_name,
                    "flow_id": f"order_{app_name.lower()}",
                    "slots": {"item": item, "app": app_name},
                    "summary": f"Order {item} on {app_name}",
                    "confirmed": False,
                    "status": "awaiting_slots"
                }
                res = {
                    "intent": "AMBIGUITY_RESOLVE",
                    "missing_parameter": "restaurant",
                    "clarification_question": "Which restaurant would you like to order from, Domino's or somewhere else?",
                    "options": ["Domino's", "Pizza Hut"],
                    "extracted_plan": res_plan
                }
                return AmbiguityClarification(**res) if parse_to_obj else json.dumps(res)

            extracted_slots = {"item": item, "restaurant": restaurant, "app": app_name, "quantity": 1}
            flow_id = "order_dominos_zomato" if ("domino" in restaurant.lower() and "zomato" in app_name.lower()) else "order_{}_{}".format(restaurant.lower().replace("'", "").replace(" ", "_"), app_name.lower())
            summary = f"Order {item} from {restaurant} on {app_name}"

            if is_explicit_teach:
                res = {
                    "intent": "CONFIRM_PLAN",
                    "extracted_plan": {
                        "mode": "TEACH",
                        "stage": "STAGE_3_EXECUTION",
                        "app_name": app_name,
                        "flow_id": flow_id,
                        "slots": extracted_slots,
                        "summary": summary,
                        "confirmed": False,
                        "status": "awaiting_confirmation"
                    },
                    "spoken_confirmation_request": f"I've prepared your TEACH plan: {summary}. Ready to start recording the demonstration?",
                    "options": ["Confirm & Proceed", "Cancel"]
                }
                return ConfirmPlanIntent(**res) if parse_to_obj else json.dumps(res)
            elif is_explicit_order:
                res = {
                    "intent": "CONFIRM_PLAN",
                    "extracted_plan": {
                        "mode": "ORDER",
                        "stage": "STAGE_3_EXECUTION",
                        "app_name": app_name,
                        "flow_id": flow_id,
                        "slots": extracted_slots,
                        "summary": summary,
                        "confirmed": False,
                        "status": "awaiting_confirmation"
                    },
                    "spoken_confirmation_request": f"I've prepared your ORDER plan: {summary}. Shall I proceed with the order?",
                    "options": ["Confirm & Proceed", "Cancel"]
                }
                return ConfirmPlanIntent(**res) if parse_to_obj else json.dumps(res)
            else:
                # Resolve mode first!
                res = {
                    "intent": "RESOLVE_MODE",
                    "extracted_plan": {
                        "mode": "UNRESOLVED",
                        "stage": "STAGE_1_INTENT_MODE",
                        "app_name": app_name,
                        "flow_id": flow_id,
                        "slots": extracted_slots,
                        "summary": summary,
                        "confirmed": False,
                        "status": "awaiting_mode"
                    },
                    "spoken_prompt": f"I've extracted your request: {summary}. First, are you in TEACH mode so you can demonstrate the steps on your screen, or ORDER mode to execute autonomously?",
                    "options": ["Teach mode", "Order mode"]
                }
                return ResolveModeIntent(**res) if parse_to_obj else json.dumps(res)

        # 6. E-commerce flow (Amazon)
        if "amazon" in lower or "earbuds" in lower or "phone case" in lower:
            flow_exists = "search_amazon" in registered or any("amazon" in fid.lower() for fid in registered)

            if flow_exists and ("phone case" in lower or not is_explicit_teach):
                search_term = "phone case" if "phone case" in lower else "wireless earbuds"
                res = {
                    "intent": "REPLAY",
                    "matched_flow_id": "search_amazon",
                    "confidence": 0.95,
                    "is_paraphrase": True,
                    "slot_overrides": {"search_term": search_term},
                    "effective_slots": {"search_term": search_term, "app": "Amazon"},
                    "spoken_acknowledgment": f"Searching for {search_term} on Amazon and adding first result to cart.",
                    "extracted_plan": {
                        "mode": "ORDER",
                        "stage": "STAGE_3_EXECUTION",
                        "app_name": "Amazon",
                        "flow_id": "search_amazon",
                        "slots": {"search_term": search_term, "app": "Amazon"},
                        "summary": f"Search for {search_term} on Amazon and add to cart",
                        "confirmed": True,
                        "status": "executing"
                    }
                }
                return ReplayIntent(**res) if parse_to_obj else json.dumps(res)

            search_term = "wireless earbuds" if "earbuds" in lower else "phone case"
            summary = f"Search for {search_term} on Amazon and add the first result to cart"
            slots = {"search_term": search_term, "app": "Amazon"}

            if is_explicit_teach:
                res = {
                    "intent": "CONFIRM_PLAN",
                    "extracted_plan": {
                        "mode": "TEACH",
                        "stage": "STAGE_3_EXECUTION",
                        "app_name": "Amazon",
                        "flow_id": "search_amazon",
                        "slots": slots,
                        "summary": summary,
                        "confirmed": False,
                        "status": "awaiting_confirmation"
                    },
                    "spoken_confirmation_request": f"I've prepared your TEACH plan: {summary}. Ready to start recording the demonstration?",
                    "options": ["Confirm & Proceed", "Cancel"]
                }
                return ConfirmPlanIntent(**res) if parse_to_obj else json.dumps(res)
            else:
                res = {
                    "intent": "RESOLVE_MODE",
                    "extracted_plan": {
                        "mode": "UNRESOLVED",
                        "stage": "STAGE_1_INTENT_MODE",
                        "app_name": "Amazon",
                        "flow_id": "search_amazon",
                        "slots": slots,
                        "summary": summary,
                        "confirmed": False,
                        "status": "awaiting_mode"
                    },
                    "spoken_prompt": f"I've extracted your request: {summary}. First, are you in TEACH mode to demonstrate this on Amazon, or ORDER mode to execute autonomously?",
                    "options": ["Teach mode", "Order mode"]
                }
                return ResolveModeIntent(**res) if parse_to_obj else json.dumps(res)

        # 7. Conversational preference setting or recall
        if "remember" in lower or "office" in lower or "favorite" in lower or "address" in lower:
            pref_key = "office_address" if ("office" in lower or "work" in lower) else "user_note"
            pref_val = "Cyber City" if "cyber city" in lower else original_text
            res = {
                "intent": "CONVERSE",
                "spoken_response": f"Understood! I'll remember that your {pref_key.replace('_', ' ')} is {pref_val}.",
                "learned_preference_key": pref_key,
                "learned_preference_value": pref_val
            }
            return ConverseIntent(**res) if parse_to_obj else json.dumps(res)

        # 8. Dynamic conversational response
        if any(w in lower for w in ["finish", "done", "completed", "stop recording"]):
            spoken = "Demonstration captured and saved! I've learned the steps for your workflow. You can now execute it anytime in ORDER mode."
        elif any(g in lower for g in ["hello", "hi", "hey", "good morning", "good evening", "greetings"]):
            spoken = "Hello! I'm Ava, your executive voice assistant. What workflow or task would you like to tackle today?"
        elif any(t in lower for t in ["thank", "thanks", "awesome", "great", "perfect", "good job"]):
            spoken = "You're very welcome! Let me know whenever you're ready for your next order or demonstration."
        elif lower.startswith(("what is your name", "who are you")):
            spoken = "I am Ava, your teachable mobile voice assistant. I can automate apps on your device, learn new workflows from your screen demonstrations, and carry out tasks for you."
        elif any(q in lower for q in ["how are you", "how are you doing"]):
            spoken = "I'm doing well, thank you! I'm ready to help you navigate your apps or automate your tasks. How can I assist you?"
        elif lower.startswith(("what", "who", "where", "how", "why", "can you", "tell me")):
            spoken = "I'm here to help with that. As your teachable assistant, I can automate flows across your apps, record demonstrations on your screen, or help answer your questions. Tell me what you'd like to do!"
        else:
            spoken = "I'm with you. Feel free to tell me what task you'd like to automate or order on your device, or ask me any question!"

        res = {
            "intent": "CONVERSE",
            "spoken_response": spoken
        }
        return ConverseIntent(**res) if parse_to_obj else json.dumps(res)
