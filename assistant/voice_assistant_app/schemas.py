from typing import Literal, Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

class ExtractedPlan(BaseModel):
    mode: Literal["TEACH", "ORDER", "UNRESOLVED"] = Field(
        default="UNRESOLVED",
        description="Mode of operation: TEACH (record user demonstration) or ORDER (autonomous execution)"
    )
    stage: Literal["STAGE_1_INTENT_MODE", "STAGE_2_SLOTS", "STAGE_3_EXECUTION", "COMPLETED"] = Field(
        default="STAGE_1_INTENT_MODE",
        description="Sequential workflow stage"
    )
    app_name: Optional[str] = Field(default="", description="Target mobile application (e.g. Zomato, Domino's, Amazon)")
    flow_id: Optional[str] = Field(default="", description="Clean snake_case unique ID for the flow")
    slots: Dict[str, Any] = Field(default_factory=dict, description="Extracted parameters e.g. item, restaurant, quantity, address")
    summary: Optional[str] = Field(default="", description="Human-readable summary of the intended action")
    confirmed: bool = Field(default=False, description="Whether the user has confirmed this plan")
    status: Literal["gathering_info", "awaiting_slots", "awaiting_mode", "awaiting_confirmation", "confirmed", "executing", "recording", "completed"] = Field(
        default="awaiting_mode",
        description="Current intake workflow status"
    )

    def are_three_slots_complete(self) -> bool:
        """Check if all three canonical slots are satisfied: item, venue/brand, app."""
        p_slots = self.slots or {}
        has_item = bool(p_slots.get("item") or p_slots.get("search_term"))
        is_retail = bool(self.app_name and self.app_name.lower() in ["amazon", "flipkart", "ebay", "walmart", "uber", "ola"]) or bool(p_slots.get("app") and str(p_slots.get("app")).lower() in ["amazon", "flipkart", "ebay", "walmart", "uber", "ola"])
        has_venue = bool(p_slots.get("restaurant") or p_slots.get("brand") or p_slots.get("target_action") or p_slots.get("action") or is_retail)
        has_app = bool(self.app_name or p_slots.get("app"))
        return has_item and has_venue and has_app

    def get_missing_core_slots(self) -> List[str]:
        missing = []
        p_slots = self.slots or {}
        if not (p_slots.get("item") or p_slots.get("search_term")):
            missing.append("item")
        is_retail = bool(self.app_name and self.app_name.lower() in ["amazon", "flipkart", "ebay", "walmart", "uber", "ola"]) or bool(p_slots.get("app") and str(p_slots.get("app")).lower() in ["amazon", "flipkart", "ebay", "walmart", "uber", "ola"])
        if not (p_slots.get("restaurant") or p_slots.get("brand") or p_slots.get("target_action") or p_slots.get("action") or is_retail):
            missing.append("restaurant")
        if not (self.app_name or p_slots.get("app")):
            missing.append("app")
        return missing

class ResolveModeIntent(BaseModel):
    intent: Literal["RESOLVE_MODE"] = "RESOLVE_MODE"
    extracted_plan: ExtractedPlan = Field(default_factory=ExtractedPlan)
    spoken_prompt: str = Field(
        default="Would you like to teach me this workflow by demonstrating it, or order it autonomously?",
        description="Spoken question asking user to resolve TEACH vs ORDER mode"
    )
    options: List[str] = Field(default_factory=lambda: ["Teach mode", "Order mode"])

class ConfirmPlanIntent(BaseModel):
    intent: Literal["CONFIRM_PLAN"] = "CONFIRM_PLAN"
    extracted_plan: ExtractedPlan = Field(description="Complete extracted plan to be confirmed")
    spoken_confirmation_request: str = Field(
        description="Spoken prompt asking user to confirm the extracted plan before proceeding"
    )
    options: List[str] = Field(default_factory=lambda: ["Confirm & Proceed", "Change details", "Cancel"])

class TeachIntent(BaseModel):
    intent: Literal["TEACH"] = "TEACH"
    flow_id: str = Field(default="flow_learned", description="Clean snake_case unique ID")
    app_name: str = Field(default="App", description="Target mobile app")
    initial_trigger_phrase: str = Field(default="", description="Exact user command that initiated learning")
    extracted_slots: Dict[str, Any] = Field(default_factory=dict, description="Initial parameters found in command")
    spoken_confirmation: str = Field(default="Learned new flow.", description="Voice confirmation")
    extracted_plan: Optional[ExtractedPlan] = None

class ReplayIntent(BaseModel):
    intent: Literal["REPLAY"] = "REPLAY"
    matched_flow_id: str = Field(default="", description="ID of matching registered flow")
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    is_exact_match: bool = Field(default=False)
    is_paraphrase: bool = Field(default=False)
    slot_overrides: Dict[str, Any] = Field(default_factory=dict)
    effective_slots: Dict[str, Any] = Field(default_factory=dict)
    spoken_acknowledgment: str = Field(default="Starting workflow.", description="Spoken acknowledgment")
    extracted_plan: Optional[ExtractedPlan] = None

class AmbiguityClarification(BaseModel):
    intent: Literal["AMBIGUITY_RESOLVE"] = "AMBIGUITY_RESOLVE"
    missing_parameter: Optional[str] = Field(default="restaurant")
    candidate_flow_ids: List[str] = Field(default_factory=list)
    clarification_question: str = Field(default="Which restaurant or item would you like to order?")
    options: List[str] = Field(default_factory=list)
    extracted_plan: Optional[ExtractedPlan] = None

class UnknownIntent(BaseModel):
    intent: Literal["UNKNOWN"] = "UNKNOWN"
    raw_query: str = Field(default="")
    spoken_response: str = Field(
        default="I haven't learned how to do that yet. Would you like to teach me?"
    )
    extracted_plan: Optional[ExtractedPlan] = None

class StatusQueryIntent(BaseModel):
    intent: Literal["STATUS_QUERY"] = "STATUS_QUERY"
    target: Literal["last_run", "current_step", "all_flows"] = "last_run"
    spoken_response: str = Field(default="Status checked.")

class ConverseIntent(BaseModel):
    intent: Literal["CONVERSE"] = "CONVERSE"
    spoken_response: str = Field(description="Natural, intelligent conversational reply")
    learned_preference_key: Optional[str] = Field(default=None, description="Optional key if user stated a preference e.g. favorite_food, work_address")
    learned_preference_value: Optional[str] = Field(default=None)
    extracted_plan: Optional[ExtractedPlan] = None

class ExecutionIntervention(BaseModel):
    intent: Literal["EXECUTION_INTERVENTION"] = "EXECUTION_INTERVENTION"
    trigger_type: Literal["credential_boundary", "stuck_state", "screen_change_popup"]
    spoken_prompt: str = Field(default="Attention required.")
    action_required_from_user: bool = Field(default=True)

VoiceDecision = Union[
    ResolveModeIntent,
    ConfirmPlanIntent,
    TeachIntent,
    ReplayIntent,
    AmbiguityClarification,
    UnknownIntent,
    StatusQueryIntent,
    ConverseIntent,
    ExecutionIntervention
]

class OrchestratorResponse(BaseModel):
    decision: VoiceDecision = Field(discriminator="intent")
    extracted_plan: Optional[ExtractedPlan] = None
    current_stage: Optional[str] = None
    matched_testcase: Optional[str] = None
    raw_text: str = Field(description="User transcript")
    processing_time_ms: float = Field(default=0.0)
