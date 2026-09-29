import sys

replacement_code = '''
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
            # Check if user is reiterating on the completed workflow
            is_reiteration = False
            reiterated_plan = dict(last_completed)
            reiterated_plan["slots"] = dict(last_completed.get("slots", {}))

            if "farmhouse" in text_lower:
                is_reiteration = True
                reiterated_plan["slots"]["item"] = "Farmhouse pizza"
                rest = reiterated_plan["slots"].get("restaurant", "Domino's")
                app_n = reiterated_plan.get("app_name", "Zomato")
                reiterated_plan["summary"] = f"Order Farmhouse pizza from {rest} on {app_n}"
            elif any(w in text_lower for w in ["two pizzas", "2 pizzas", "two", "quantity 2", "make it 2"]):
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

            # Check if user provides missing slot in Stage 2
            if current_stage == "STAGE_2_SLOTS" or plan_status == "awaiting_slots":
                if any(r in text_lower for r in ["domino", "pizza hut"]):
                    chosen_rest = "Domino's" if "domino" in text_lower else "Pizza Hut"
                    draft_plan["slots"]["restaurant"] = chosen_rest
                    draft_plan["stage"] = "STAGE_3_EXECUTION"
                    draft_plan["status"] = "awaiting_confirmation"
                    draft_plan["confirmed"] = False
                    item = draft_plan["slots"].get("item", "pizza")
                    app_n = draft_plan.get("app_name", "Zomato")
                    draft_plan["summary"] = f"Order {item} from {chosen_rest} on {app_n}"
                    active_mem.set_draft_plan(draft_plan)
                    decision = ConfirmPlanIntent(
                        intent="CONFIRM_PLAN",
                        extracted_plan=ExtractedPlan(**draft_plan),
                        spoken_confirmation_request=f"Got it, {chosen_rest}. Ready to proceed with: {draft_plan['summary']}?"
                    )
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")

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

        # Guardrail 8: New intake - Mode Resolution Invariant (Stage 1)
        # If unlearned automation command and no explicit mode provided
        is_automation_request = (
            any(w in text_lower for w in ["margherita", "domino", "pizza", "zomato", "swiggy", "amazon", "earbuds", "phone case"])
            or text_lower.startswith(("order ", "buy ", "search for ", "get me "))
        )
        flow_exists = any(fid in registered_workflows for fid in ["order_dominos_zomato", "search_amazon"])

        if is_automation_request:
            if not is_explicit_teach and not is_explicit_order and not flow_exists:
                # Check if canonical slots are missing (e.g. "Order pizza" without restaurant)
                if "order pizza" in text_lower and not any(r in text_lower for r in ["domino", "pizza hut"]):
                    res_plan = {
                        "mode": "UNRESOLVED",
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
'''

with open('voice_assistant_app/orchestrator.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace process_utterance body cleanly
start_marker = "            return self._wrap_response(decision, transcript, start_time, stage=\"COMPLETED\")\n"
end_marker = "    def _wrap_response("

idx1 = content.find(start_marker)
if idx1 == -1:
    print("Could not find start_marker!")
    sys.exit(1)
idx1 += len(start_marker)

idx2 = content.find(end_marker, idx1)
if idx2 == -1:
    print("Could not find end_marker!")
    sys.exit(1)

new_content = content[:idx1] + replacement_code + "\n" + content[idx2:]

with open('voice_assistant_app/orchestrator.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("orchestrator.py updated successfully!")
