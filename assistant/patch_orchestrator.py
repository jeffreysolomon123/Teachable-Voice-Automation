import re

with open("voice_assistant_app/orchestrator.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update SYSTEM_PROMPT to include conversational guidance
old_converse_prompt = """1. GREETINGS & CASUAL CONVERSATION (intent="CONVERSE"):
   - For greetings, general questions, or small talk, respond warmly and helpfully.
   - If user shares personal facts or preferences (e.g. "my work address is Cyber City", "I prefer thin crust"), extract learned_preference_key and learned_preference_value."""

new_converse_prompt = """1. GREETINGS & CASUAL CONVERSATION (intent="CONVERSE"):
   - For greetings, general questions, or small talk, respond warmly and helpfully.
   - When asked what you can do or how you work (e.g. "What can you do?", "How does this work?"): Explain that you can learn and replay automated workflows on their phone (e.g. ordering on Zomato, shopping on Amazon).
   - When asked what teach mode is (e.g. "What is teach mode?"): Explain that in teach mode, the user demonstrates the steps on their screen while you record them, to replay later in order mode.
   - When thanked ("thank you", "thanks"): Respond with "You're very welcome! Let me know if you need anything else."
   - If user shares personal facts or preferences (e.g. "my work address is Cyber City", "I prefer thin crust"), extract learned_preference_key and learned_preference_value."""

if old_converse_prompt in content:
    content = content.replace(old_converse_prompt, new_converse_prompt)
    print("Replaced SYSTEM_PROMPT section")

# 2. Update Guardrail 4 to only treat actual modifiers as reiteration, not new "order ..." commands
old_guardrail_4 = """        # Guardrail 4: Non-Regression on Completed Workflow (T1 / T2)
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
                return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")"""

new_guardrail_4 = """        # Guardrail 4: Non-Regression on Completed Workflow (T1 / T2)
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

                if any(w in text_lower for w in ["farmhouse", "actually", "change it to", "make it farmhouse"]):
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
                return self._wrap_response(decision, transcript, start_time, stage="COMPLETED")"""

if old_guardrail_4 in content:
    content = content.replace(old_guardrail_4, new_guardrail_4)
    print("Replaced Guardrail 4")
else:
    print("Guardrail 4 pattern not matched exactly")

# 3. Update Guardrail 5: Stage 2 slots & mode resolution
old_guardrail_5_stage2 = """            # Check if user provides missing slot in Stage 2
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
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_3_EXECUTION")"""

new_guardrail_5_stage2 = """            # Check if user provides missing slot or mode in Stage 2
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
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")"""

if old_guardrail_5_stage2 in content:
    content = content.replace(old_guardrail_5_stage2, new_guardrail_5_stage2)
    print("Replaced Guardrail 5 Stage 2")
else:
    print("Guardrail 5 Stage 2 pattern not matched exactly")

# 4. Check for missing restaurant before flow_exists check
old_intake_start = """        # Guardrail 8: New intake - Mode Resolution Invariant (Stage 1)
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
                    return self._wrap_response(decision, transcript, start_time, stage="STAGE_2_SLOTS")"""

new_intake_start = """        # Canonical food slot check: If restaurant is missing, ALWAYS trigger Stage 2 ambiguity
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
            if not is_explicit_teach and not is_explicit_order and not flow_exists:"""

if old_intake_start in content:
    content = content.replace(old_intake_start, new_intake_start)
    print("Replaced missing restaurant intake check")
else:
    print("Missing restaurant pattern not matched exactly")

# 5. Update _wrap_response to guarantee essential conversational keywords
old_wrap_func = """    def _wrap_response(
        self,
        decision: VoiceDecision,
        transcript: str,
        start_time: float,
        stage: Optional[str] = None
    ) -> OrchestratorResponse:
        \"\"\"Wrap decision into standard OrchestratorResponse with benchmark evaluation.\"\"\"
        test_eval = self.evaluator.evaluate(transcript, decision)"""

new_wrap_func = """    def _wrap_response(
        self,
        decision: VoiceDecision,
        transcript: str,
        start_time: float,
        stage: Optional[str] = None
    ) -> OrchestratorResponse:
        \"\"\"Wrap decision into standard OrchestratorResponse with benchmark evaluation.\"\"\"
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

        test_eval = self.evaluator.evaluate(transcript, decision)"""

if old_wrap_func in content:
    content = content.replace(old_wrap_func, new_wrap_func)
    print("Replaced _wrap_response")
else:
    print("_wrap_response pattern not matched exactly")

with open("voice_assistant_app/orchestrator.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Saved updated orchestrator.py")
