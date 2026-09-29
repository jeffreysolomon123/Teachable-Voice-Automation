import asyncio
import time
from typing import Dict, Any, Optional
from voice_assistant_app.schemas import OrchestratorResponse, VoiceDecision
from voice_assistant_app.stt_service import STTService
from voice_assistant_app.tts_service import TTSService
from voice_assistant_app.orchestrator import VoiceOrchestrator
from voice_assistant_app.workflow_memory import WorkflowMemory
from voice_assistant_app.session_memory import SessionMemory, SessionManager

class TurnManager:
    """Manages conversational turn-taking, per-session state, and audio cues."""

    def __init__(
        self,
        orchestrator: Optional[VoiceOrchestrator] = None,
        stt: Optional[STTService] = None,
        tts: Optional[TTSService] = None,
        memory: Optional[WorkflowMemory] = None,
        session_memory: Optional[SessionMemory] = None,
        session_manager: Optional[SessionManager] = None
    ):
        self.memory = memory or WorkflowMemory()
        self.session_memory = session_memory or SessionMemory()
        self.session_manager = session_manager or SessionManager()
        self.orchestrator = orchestrator or VoiceOrchestrator(
            memory=self.memory,
            session_memory=self.session_memory
        )
        self.stt = stt or STTService()
        self.tts = tts or TTSService()

        self.current_state = "IDLE"
        self.active_flow_id: Optional[str] = None

    def _resolve_session(self, session_id: Optional[str] = None) -> SessionMemory:
        if not session_id or session_id == "default":
            return self.session_memory
        return self.session_manager.get_session(session_id)

    def get_state(self, session_id: str = "default") -> Dict[str, Any]:
        """Return conversational state, memory, and telemetry for a session."""
        active_sess = self._resolve_session(session_id)
        return {
            "session_id": active_sess.session_id,
            "state": self.current_state,
            "active_flow_id": self.active_flow_id,
            "history": active_sess.get_recent_history(limit=10),
            "preferences": active_sess.get_all_preferences(),
            "has_pending_clarification": active_sess.get_pending_clarification() is not None,
            "draft_plan": active_sess.get_draft_plan(),
            "last_run": self.memory.get_last_run_status()
        }

    async def process_user_text(
        self,
        text: str,
        voice_override: Optional[str] = None,
        session_id: str = "default"
    ) -> Dict[str, Any]:
        """Process user text turn with multi-turn memory logging and session isolation."""
        active_sess = self._resolve_session(session_id)
        self.current_state = "THINKING"

        # Record user turn in conversational memory
        active_sess.add_message("user", text)

        # Retrieve any pending clarification context
        pending = active_sess.get_pending_clarification()
        session_context = {"pending_intent": pending} if pending else {}

        response: OrchestratorResponse = self.orchestrator.process_utterance(
            transcript=text,
            session_context=session_context,
            session_memory=active_sess
        )
        decision: VoiceDecision = response.decision

        spoken_text = ""
        earcon_cue = "PING"

        if decision.intent == "RESOLVE_MODE":
            self.current_state = "AWAITING_MODE"
            spoken_text = decision.spoken_prompt
            earcon_cue = "QUESTION_CHIME"

        elif decision.intent == "CONFIRM_PLAN":
            self.current_state = "AWAITING_CONFIRMATION"
            spoken_text = decision.spoken_confirmation_request
            earcon_cue = "QUESTION_CHIME"

        elif decision.intent == "TEACH":
            self.current_state = "AWAITING_DEMO"
            self.active_flow_id = decision.flow_id
            spoken_text = decision.spoken_confirmation
            active_sess.set_pending_clarification(None)
            earcon_cue = "HANDOFF"

        elif decision.intent == "REPLAY":
            self.current_state = "EXECUTING_FLOW"
            self.active_flow_id = decision.matched_flow_id
            spoken_text = decision.spoken_acknowledgment
            active_sess.set_pending_clarification(None)
            self.memory.record_run_result(
                flow_id=decision.matched_flow_id,
                status="IN_PROGRESS",
                step_stopped="started",
                details=f"Executing with slots: {decision.effective_slots}"
            )

        elif decision.intent == "AMBIGUITY_RESOLVE":
            self.current_state = "AWAITING_CLARIFICATION"
            spoken_text = decision.clarification_question
            active_sess.set_pending_clarification({
                "missing_parameter": decision.missing_parameter,
                "candidate_flow_ids": decision.candidate_flow_ids
            })
            earcon_cue = "QUESTION_CHIME"

        elif decision.intent == "UNKNOWN":
            self.current_state = "IDLE"
            spoken_text = decision.spoken_response
            active_sess.set_pending_clarification(None)

        elif decision.intent == "STATUS_QUERY":
            self.current_state = "IDLE"
            spoken_text = decision.spoken_response
            active_sess.set_pending_clarification(None)

        elif decision.intent == "CONVERSE":
            self.current_state = "IDLE"
            spoken_text = decision.spoken_response

        elif decision.intent == "EXECUTION_INTERVENTION":
            spoken_text = decision.spoken_prompt
            if decision.trigger_type == "credential_boundary":
                self.current_state = "HANDOFF_PAUSED"
                earcon_cue = "HANDOFF_ALERT"
            elif decision.trigger_type == "stuck_state":
                self.current_state = "AWAITING_CLARIFICATION"
                earcon_cue = "STUCK_ALERT"
            else:
                self.current_state = "AWAITING_CLARIFICATION"

        # Record assistant reply in conversational memory
        active_sess.add_message("assistant", spoken_text, metadata={"intent": decision.intent})

        # Synthesize expressive neural audio
        audio_base64 = ""
        try:
            audio_bytes = await self.tts.synthesize_to_bytes(spoken_text, voice=voice_override)
            import base64
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as e:
            print(f"[TurnManager] TTS error: {e}")

        plan_dict = response.extracted_plan.model_dump() if response.extracted_plan else None
        if not plan_dict and hasattr(decision, "extracted_plan") and decision.extracted_plan:
            plan_dict = decision.extracted_plan.model_dump() if hasattr(decision.extracted_plan, "model_dump") else decision.extracted_plan

        return {
            "session_id": active_sess.session_id,
            "state": self.current_state,
            "current_stage": response.current_stage or (plan_dict.get("stage") if plan_dict else "IDLE"),
            "earcon_cue": earcon_cue,
            "transcript": text,
            "spoken_text": spoken_text,
            "audio_base64": audio_base64,
            "decision": decision.model_dump(),
            "extracted_plan": plan_dict,
            "matched_testcase": response.matched_testcase,
            "processing_time_ms": response.processing_time_ms,
            "preferences": active_sess.get_all_preferences()
        }

    async def process_user_audio(
        self,
        audio_bytes: bytes,
        filename: str = "voice_input.webm",
        voice_override: Optional[str] = None,
        session_id: str = "default"
    ) -> Dict[str, Any]:
        """Full pipeline: Audio -> Whisper/Gemini STT with prompt biasing -> Orchestrator -> Neural TTS."""
        self.current_state = "THINKING"
        transcript = self.stt.transcribe_audio_bytes(audio_bytes, filename=filename)
        if not transcript or not transcript.strip():
            active_sess = self._resolve_session(session_id)
            spoken = "I didn't quite catch that. Could you please say that again?"
            return {
                "session_id": active_sess.session_id,
                "state": "IDLE",
                "current_stage": "IDLE",
                "earcon_cue": "PING",
                "transcript": "",
                "spoken_text": spoken,
                "audio_base64": "",
                "decision": {"intent": "CONVERSE", "spoken_response": spoken},
                "extracted_plan": None,
                "matched_testcase": None,
                "processing_time_ms": 10.0,
                "preferences": active_sess.get_all_preferences()
            }
        return await self.process_user_text(transcript, voice_override=voice_override, session_id=session_id)

    def trigger_execution_event(self, trigger_type: str, details: str = "") -> Dict[str, Any]:
        """Downstream event notification (pop-up, stuck, credential boundary)."""
        prompt = ""
        if trigger_type == "credential_boundary":
            self.current_state = "HANDOFF_PAUSED"
            prompt = "I've reached the payment screen. Your turn to complete the payment."
            self.memory.record_run_result(
                flow_id=self.active_flow_id or "unknown",
                status="PAUSED_AT_PAYMENT",
                step_stopped="payment_screen",
                details="Handed over to user for payment"
            )
        elif trigger_type == "stuck_state":
            self.current_state = "AWAITING_CLARIFICATION"
            prompt = f"I'm stuck: {details}. How should I proceed?" if details else "I'm not sure which button to press. Should I click Delivery or Takeaway?"
            self.memory.record_run_result(
                flow_id=self.active_flow_id or "unknown",
                status="STUCK_IN_EXECUTION",
                step_stopped="tab_selection",
                details=details or "Ambiguous button state"
            )
        else:
            self.current_state = "AWAITING_CLARIFICATION"
            prompt = "A system pop-up appeared. Please resolve it on screen."

        return {
            "state": self.current_state,
            "current_state": self.current_state,
            "spoken_prompt": prompt,
            "trigger_type": trigger_type
        }
