"""
Genesis Communication Interface — Data Models & Schemas
Strictly validated request/response schemas for local web communication.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class IndividualChatRequest(BaseModel):
    recipient: str = Field(..., description="Target participant: 'Genesis', 'Antigravity', or 'OpenAI-Agent'")
    message: str = Field(..., min_length=1, description="Message content from Fodoh")
    is_live: bool = Field(default=False, description="Whether to use live API calls (requires Fodoh approval)")


class TriadInitiateRequest(BaseModel):
    probe_prompt: str = Field(
        default="Review our multi-agent architecture and division of responsibilities across Intent, Execution, and Critique.",
        min_length=1,
        description="Initial probe to kick off the supervised triad round",
    )
    is_live: bool = Field(default=False, description="Whether to use live API calls")


class TriadGateResponseRequest(BaseModel):
    approval_id: str = Field(..., description="ID of the pending approval checkpoint")
    action: str = Field(..., description="'approve' or 'reject'")
    edited_payload: Optional[str] = Field(default=None, description="Optional edited message payload to forward")


class KillSwitchRequest(BaseModel):
    action: str = Field(..., description="'trigger' or 'clear'")


class JournalActionRequest(BaseModel):
    action: str = Field(..., description="'start', 'break', 'resume', or 'stop'")
    activity: Optional[str] = Field(default="", description="Activity description for 'start'")
    milestone: Optional[str] = Field(default="", description="Milestone for 'start'")
    reason: Optional[str] = Field(default="", description="Reason for 'break'")
    summary: Optional[str] = Field(default="", description="Summary for 'stop'")


class ChatTurnResponse(BaseModel):
    turn_id: int
    speaker: str
    recipient: Optional[str] = None
    content: str
    timestamp: str
    latency_sec: float = 0.0
    cost_usd_est: float = 0.0
    tokens_estimated: int = 0
    model: str = "mock"
    finish_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryReviewRequest(BaseModel):
    memory_id: str = Field(..., description="ID of the memory in staging")
    action: str = Field(..., description="'approve', 'reject', 'edit', or 'delete'")
    edited_content: Optional[str] = Field(default=None, description="Updated memory text if action is 'edit'")


class MemoryProposeRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Candidate memory statement")
    speaker: str = Field(..., description="Proposing speaker ('Antigravity', 'OpenAI-Agent', 'Genesis', 'Fodoh')")
    category: str = Field(..., description="Memory category")
    session_id: str = Field(..., description="Source session ID")
    turn_id: Optional[int] = Field(default=None, description="Source turn index")
    transcript_path: Optional[str] = Field(default=None, description="Path to original transcript")
    authorization_record: Optional[str] = Field(default=None, description="Backend gate authorization record")
    authorization_scope: Optional[str] = Field(default=None, description="Authorization scope ('COMMUNICATION_TRANSIT' or 'MEMORY_APPROVAL')")
    is_synthetic: bool = Field(default=False, description="Whether the conversation was synthetic offline")
    rationale: Optional[str] = Field(default="", description="Why this memory is significant")


# -----------------------------------------------------------------------------
# Phase 2: Flexible Multi-Round Collaboration Models
# -----------------------------------------------------------------------------

class GroupTurnRecord(BaseModel):
    turn_id: str = Field(..., description="Unique turn identifier")
    turn_index: int = Field(..., description="Zero-based sequence index")
    speaker: str = Field(..., description="Speaker name: 'Fodoh', 'Antigravity', 'OpenAI-Agent', 'Genesis'")
    speaker_type: str = Field(default="AGENT", description="'HUMAN' or 'AGENT'")
    content: str = Field(..., description="Turn text content")
    timestamp: str = Field(..., description="ISO-8601 timestamp")
    model: str = Field(default="mock", description="Model name or 'human'")
    cost_usd_est: float = Field(default=0.0, description="Estimated cost in USD")
    latency_sec: float = Field(default=0.0, description="Turn latency in seconds")
    idempotency_token: str = Field(..., description="Unique token to prevent duplicate insertion")
    status: str = Field(default="COMMITTED", description="'COMMITTED', 'FAILED', or 'UNKNOWN_OUTCOME'")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provenance metadata")


class GroupSessionState(BaseModel):
    session_id: str = Field(..., description="Unique session ID")
    title: str = Field(default="Collaborative Topic", description="Topic title")
    created_at: str = Field(..., description="Creation ISO-8601 timestamp")
    updated_at: str = Field(..., description="Last update ISO-8601 timestamp")
    status: str = Field(
        default="WAITING_FOR_HUMAN",
        description="'WAITING_FOR_HUMAN', 'DISPATCH_PENDING', 'EXECUTING_TURN', 'UNKNOWN_OUTCOME', 'PAUSED', 'FAILED', 'CANCELLED', 'COMPLETED'",
    )
    is_live: bool = Field(default=False, description="Whether session permits live paid API calls")
    max_turns: int = Field(default=10, ge=1, le=20, description="Bounded turn cap for this session")
    turns: List[GroupTurnRecord] = Field(default_factory=list, description="Ordered chronological transcript")
    active_gate: Optional[Dict[str, Any]] = Field(default=None, description="Pending human approval gate")
    in_flight_reservation_id: Optional[str] = Field(default=None, description="Active budget reservation ID")
    cumulative_cost_usd: float = Field(default=0.0, description="Total metered API spending for this session")
    recommended_next_speaker: Optional[str] = Field(default=None, description="Suggested next participant")
    error_message: Optional[str] = Field(default=None, description="Diagnostic error message if failed")


class GroupSessionCreateRequest(BaseModel):
    title: str = Field(default="Collaborative Group Session", min_length=1, description="Topic title")
    initial_prompt: Optional[str] = Field(default=None, description="Optional opening question from Fodoh")
    is_live: bool = Field(default=False, description="Whether session permits live paid API calls")
    max_turns: int = Field(default=10, ge=1, le=20, description="Maximum bounded turns")


class GroupTurnPrepareRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    target_agent: str = Field(..., description="Agent to invoke: 'Antigravity', 'OpenAI-Agent', or 'Genesis'")
    prompt_override: Optional[str] = Field(default=None, description="Optional custom prompt/question")


class GroupTurnDispatchRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    gate_nonce: str = Field(..., description="Approval gate nonce to prevent replay attacks")
    action: str = Field(..., description="'approve' or 'reject'")
    edited_payload: Optional[str] = Field(default=None, description="Optional edited message payload to forward")


class HumanInterjectRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    message: str = Field(..., min_length=1, description="Direct human contribution ($0 cost, no model call)")
    recommended_next_speaker: Optional[str] = Field(default=None, description="Optional target for next turn")


class GroupSessionActionRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    action: str = Field(..., description="'pause', 'resume', 'terminate', or 'recover_turn'")
    recovery_action: Optional[str] = Field(default=None, description="'mark_aborted' or 'retry' if recovering")


class GroupMemoryExtractionRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    extraction_method: str = Field(default="deterministic_offline", description="'deterministic_offline' or 'custom'")
    stage_in_review_queue: bool = Field(default=True, description="Whether to stage extracted candidates into collaborative review queue")
