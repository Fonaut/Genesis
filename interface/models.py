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
    rationale: Optional[str] = Field(default="", description="Why this memory is significant")
