"""
Genesis Communication Interface — Backend Orchestrator Engine
Bridges local web endpoints with existing Communication Hub participants,
approval gates, safety governor, and the Development Journal.
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from hub.phase0 import (
    AgentTurn,
    CliAntigravityAgent,
    GenesisParticipant,
    HubSafetyError,
    MockAntigravityAgent,
    KillSwitchActiveError,
    PROJECT_ROOT,
    SandboxManager,
)
from hub.phase1 import (
    SafetyGovernor,
    CostLimitExceededError,
    OpenAIAgentParticipant,
)
from journal.manager import DevelopmentJournalManager


@dataclass
class PendingApprovalGate:
    approval_id: str
    action_name: str
    recipient: str
    proposed_payload: str
    created_at: str
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED


@dataclass
class TriadSessionState:
    session_id: str
    start_time: str
    status: str  # ACTIVE, PENDING_APPROVAL, COMPLETED, CANCELLED_BY_USER, FAILED
    is_live: bool
    probe_prompt: str
    turns: List[Dict[str, Any]] = field(default_factory=list)
    pending_gate: Optional[PendingApprovalGate] = None
    cumulative_cost_usd: float = 0.0
    error_message: Optional[str] = None


class CommunicationInterfaceEngine:
    """
    Coordinates individual agent chats and supervised triad sessions,
    enforcing UI approval gates, memory isolation, and cost ceilings.
    """

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        max_cost_usd: float = 0.05,
    ):
        self.log_dir = log_dir or (PROJECT_ROOT / "logs" / "interface")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_file = self.log_dir / "conversations.json"
        self.max_cost_usd = max_cost_usd

        # Core safety governor and journal manager
        self.governor = SafetyGovernor(max_turns=20, max_cost_usd=max_cost_usd)
        self.journal_manager = DevelopmentJournalManager()
        self.sandbox = SandboxManager()

        # In-memory active triad session state
        self.active_triad_session: Optional[TriadSessionState] = None

    # -------------------------------------------------------------------------
    # Kill Switch Controls
    # -------------------------------------------------------------------------

    @property
    def is_kill_switch_active(self) -> bool:
        stop_file = PROJECT_ROOT / ".hub_stopped"
        return stop_file.exists() or self.governor.kill_switch_active

    def trigger_kill_switch(self) -> Dict[str, Any]:
        """Activates emergency stop immediately, blocking all outbound traffic."""
        stop_file = PROJECT_ROOT / ".hub_stopped"
        stop_file.touch()
        self.governor.kill_switch_active = True

        if self.active_triad_session and self.active_triad_session.status in ("ACTIVE", "PENDING_APPROVAL"):
            self.active_triad_session.status = "CANCELLED_BY_USER"
            self.active_triad_session.error_message = "Emergency kill switch triggered by user."
            self.active_triad_session.pending_gate = None

        return {
            "status": "STOPPED",
            "message": "Emergency kill switch activated. All agent calls halted.",
        }

    def clear_kill_switch(self) -> Dict[str, Any]:
        """Clears the emergency stop after user confirmation."""
        stop_file = PROJECT_ROOT / ".hub_stopped"
        if stop_file.exists():
            stop_file.unlink()
        self.governor.kill_switch_active = False
        return {
            "status": "READY",
            "message": "Emergency kill switch cleared. System operational.",
        }

    # -------------------------------------------------------------------------
    # Individual Agent Routing (1-on-1 Mode)
    # -------------------------------------------------------------------------

    def send_individual_message(
        self,
        recipient: str,
        message: str,
        is_live: bool = False,
    ) -> Dict[str, Any]:
        """
        Sends a message from Fodoh to an individual agent and returns their response.
        Enforces strict memory isolation, cost controls, and kill switch.
        """
        if self.is_kill_switch_active:
            raise KillSwitchActiveError("Operation aborted: Emergency kill switch is active.")

        clean_message = message.strip()
        if not clean_message:
            raise ValueError("Message cannot be empty.")

        t_start = time.perf_counter()
        speaker = "Fodoh"
        reply_content = ""
        model_name = "synthetic-offline"
        thought_suppressed = None
        turn_meta: Dict[str, Any] = {}
        cost_est = 0.0

        if recipient == "Genesis":
            agent = GenesisParticipant(is_live=is_live, max_tokens=800)
            reply_text, thought, meta = agent.process_turn(
                speaker=speaker,
                incoming_message=clean_message,
                formatted_history="(Individual chat session)",
            )
            reply_content = reply_text
            thought_suppressed = thought
            turn_meta = meta
            model_name = meta.get("model", "gpt-4o-mini" if is_live else "synthetic-offline")
            cost_est = meta.get("cost_usd_est", 0.0)

            if meta.get("is_truncated") or meta.get("finish_reason") == "length":
                raise HubSafetyError(
                    f"Genesis response was truncated by token limit. Cut-off: '{reply_content[-60:]}...'"
                )

        elif recipient == "Antigravity":
            if is_live:
                agent = CliAntigravityAgent(timeout_sec=60.0)
            else:
                agent = MockAntigravityAgent(
                    response_text=(
                        f"Antigravity technical assessment for Fodoh: Received your instruction: '{clean_message}'. "
                        "All filesystem, subprocess, and sandbox controls are verified."
                    )
                )
            ag_text, ag_thought, ag_meta = agent.send_probe(
                prompt=clean_message,
                sandbox=self.sandbox,
            )
            reply_content = ag_text
            thought_suppressed = ag_thought
            turn_meta = ag_meta
            model_name = ag_meta.get("model", "gemini-3.8-flash-high" if is_live else "mock-synthetic")
            cost_est = ag_meta.get("cost_usd_est", 0.0)

        elif recipient == "OpenAI-Agent":
            agent = OpenAIAgentParticipant(is_live=is_live, max_tokens=800)
            reply_text, meta = agent.process_turn(
                speaker=speaker,
                incoming_message=clean_message,
                formatted_history="(Individual chat session)",
            )
            reply_content = reply_text
            turn_meta = meta
            model_name = meta.get("model", "gpt-4o-mini" if is_live else "synthetic-offline")
            cost_est = meta.get("cost_usd_est", 0.0)

            if meta.get("is_truncated") or meta.get("finish_reason") == "length":
                raise HubSafetyError(
                    f"OpenAI-Agent response was truncated by output token limit. Cut-off: '{reply_content[-60:]}...'"
                )

        else:
            raise ValueError(f"Unknown recipient '{recipient}'. Must be 'Genesis', 'Antigravity', or 'OpenAI-Agent'.")

        latency = time.perf_counter() - t_start

        # Record and verify spending cap
        self.governor.cumulative_cost_usd += cost_est
        if self.governor.cumulative_cost_usd > self.governor.max_cost_usd:
            raise CostLimitExceededError(
                f"Session cost ceiling exceeded: accumulated ${self.governor.cumulative_cost_usd:.5f} USD "
                f"(hard limit: ${self.governor.max_cost_usd:.5f} USD)."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        turn_data = {
            "turn_id": int(time.time()),
            "speaker": recipient,
            "recipient": speaker,
            "user_prompt": clean_message,
            "content": reply_content,
            "timestamp": now_iso,
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost_est, 6),
            "tokens_estimated": (len(clean_message) + len(reply_content)) // 4,
            "model": model_name,
            "finish_reason": turn_meta.get("finish_reason", "stop"),
            "metadata": {
                k: v for k, v in turn_meta.items()
                if k not in ("raw_preview", "debug_log")  # Sanitize sensitive paths
            },
        }

        # Append to conversations log
        self._append_conversation_record(turn_data)
        return turn_data

    # -------------------------------------------------------------------------
    # Supervised Triad Discussion (Structured Phase 1 Routing with UI Gates)
    # -------------------------------------------------------------------------

    def initiate_triad_round(
        self,
        probe_prompt: str,
        is_live: bool = False,
    ) -> TriadSessionState:
        """
        Kicks off a supervised 3-agent triad session.
        Prepares Gate 1 (Send Initial Probe to Antigravity) and waits for human approval.
        """
        if self.is_kill_switch_active:
            raise KillSwitchActiveError("Operation aborted: Emergency kill switch is active.")

        clean_probe = probe_prompt.strip()
        if not clean_probe:
            raise ValueError("Probe prompt cannot be empty.")

        now = datetime.now(timezone.utc)
        session_id = now.strftime("%Y%m%d_%H%M%S")

        gate_1 = PendingApprovalGate(
            approval_id=f"gate_1_{session_id}",
            action_name="Send Initial Probe to Antigravity",
            recipient="Antigravity",
            proposed_payload=clean_probe,
            created_at=now.isoformat(),
        )

        state = TriadSessionState(
            session_id=session_id,
            start_time=now.isoformat(),
            status="PENDING_APPROVAL",
            is_live=is_live,
            probe_prompt=clean_probe,
            turns=[],
            pending_gate=gate_1,
            cumulative_cost_usd=0.0,
        )

        self.active_triad_session = state
        return state

    def respond_triad_gate(
        self,
        approval_id: str,
        action: str,
        edited_payload: Optional[str] = None,
    ) -> TriadSessionState:
        """
        Receives human approval or rejection from the UI.
        If approved, executes the pending agent turn and sets up the next gate.
        If rejected, cancels the session safely.
        """
        if self.is_kill_switch_active:
            raise KillSwitchActiveError("Operation aborted: Emergency kill switch is active.")

        state = self.active_triad_session
        if not state or not state.pending_gate:
            raise HubSafetyError("No active triad session with a pending approval gate.")

        gate = state.pending_gate
        if gate.approval_id != approval_id:
            raise HubSafetyError(f"Approval ID mismatch. Expected '{gate.approval_id}', got '{approval_id}'.")

        # ---------------------------------------------------------------------
        # Case A: Human Rejection
        # ---------------------------------------------------------------------
        if action.lower() == "reject":
            gate.status = "REJECTED"
            state.status = "CANCELLED_BY_USER"
            state.error_message = f"Action '{gate.action_name}' was rejected by Fodoh."
            state.pending_gate = None
            self._save_triad_audit(state)
            return state

        # ---------------------------------------------------------------------
        # Case B: Human Approval
        # ---------------------------------------------------------------------
        if action.lower() != "approve":
            raise ValueError(f"Unknown action '{action}'. Must be 'approve' or 'reject'.")

        gate.status = "APPROVED"
        payload_to_use = (edited_payload.strip() if edited_payload and edited_payload.strip() else gate.proposed_payload)

        # Process next turn according to current gate
        if "gate_1" in gate.approval_id:
            self._execute_triad_turn_1(state, payload_to_use)
        elif "gate_2" in gate.approval_id:
            self._execute_triad_turn_2(state, payload_to_use)
        elif "gate_3" in gate.approval_id:
            self._execute_triad_turn_3(state, payload_to_use)
        else:
            raise HubSafetyError(f"Unknown gate identifier in approval ID '{gate.approval_id}'.")

        return state

    def _execute_triad_turn_1(self, state: TriadSessionState, payload: str) -> None:
        """Executes Turn 1 (Antigravity) and sets up Gate 2."""
        t0 = time.perf_counter()
        if state.is_live:
            agent = CliAntigravityAgent(timeout_sec=60.0)
        else:
            agent = MockAntigravityAgent(
                response_text=(
                    "To prevent overlap and maximize efficiency, our triad operates best under a clear "
                    "separation of concerns across Intent, Execution, and Critique:\n\n"
                    "1. Genesis: Intent translation, state continuity, and companion orchestration.\n"
                    "2. Antigravity: Technical execution, filesystem manipulation, and systems verification.\n"
                    "3. OpenAI-Agent: Analytical critique, cross-validation, and independent challenge."
                )
            )

        ag_response, ag_thought, ag_meta = agent.send_probe(
            prompt=payload,
            sandbox=self.sandbox,
        )
        latency = time.perf_counter() - t0

        if not ag_response or not ag_response.strip():
            state.status = "FAILED"
            state.error_message = "Antigravity returned an empty or whitespace-only reply."
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        cost = ag_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost

        turn_1 = {
            "turn_id": 1,
            "speaker": "Antigravity",
            "content": ag_response,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(ag_response)) // 4,
            "model": ag_meta.get("model", "gemini-3.8-flash-high" if state.is_live else "mock-synthetic"),
            "metadata": ag_meta,
        }
        state.turns.append(turn_1)

        # Prepare Gate 2 (Pass Antigravity reply to OpenAI-Agent)
        next_gate = PendingApprovalGate(
            approval_id=f"gate_2_{state.session_id}",
            action_name="Pass Antigravity Response to OpenAI-Agent",
            recipient="OpenAI-Agent",
            proposed_payload=f"[Antigravity]:\n{ag_response}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state.pending_gate = next_gate
        state.status = "PENDING_APPROVAL"

    def _execute_triad_turn_2(self, state: TriadSessionState, payload: str) -> None:
        """Executes Turn 2 (OpenAI-Agent) and sets up Gate 3."""
        t0 = time.perf_counter()
        agent = OpenAIAgentParticipant(is_live=state.is_live, max_tokens=800)
        openai_reply, openai_meta = agent.process_turn(
            speaker="Antigravity",
            incoming_message=payload,
            formatted_history=f"[User Probe]: {state.probe_prompt}",
        )
        latency = time.perf_counter() - t0

        if not openai_reply or not openai_reply.strip():
            state.status = "FAILED"
            state.error_message = "OpenAI-Agent returned an empty or whitespace-only reply."
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        if openai_meta.get("is_truncated") or openai_meta.get("finish_reason") == "length":
            state.status = "FAILED"
            state.error_message = f"OpenAI-Agent response truncated by output token limit: '{openai_reply[-60:]}...'"
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        cost = openai_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost

        turn_2 = {
            "turn_id": 2,
            "speaker": "OpenAI-Agent",
            "content": openai_reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(openai_reply)) // 4,
            "model": openai_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            "metadata": openai_meta,
        }
        state.turns.append(turn_2)

        ag_response = state.turns[0]["content"]
        multi_agent_payload = f"[Antigravity]:\n{ag_response}\n\n[OpenAI-Agent]:\n{openai_reply}"

        # Prepare Gate 3 (Pass Multi-Agent Context to Genesis)
        next_gate = PendingApprovalGate(
            approval_id=f"gate_3_{state.session_id}",
            action_name="Pass Multi-Agent Dialogue to Genesis",
            recipient="Genesis",
            proposed_payload=multi_agent_payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state.pending_gate = next_gate
        state.status = "PENDING_APPROVAL"

    def _execute_triad_turn_3(self, state: TriadSessionState, payload: str) -> None:
        """Executes Turn 3 (Genesis) and finalizes the triad session."""
        t0 = time.perf_counter()
        agent = GenesisParticipant(is_live=state.is_live, max_tokens=800)
        gen_reply, gen_thought, gen_meta = agent.process_turn(
            speaker="OpenAI-Agent",
            incoming_message=(
                f"Antigravity and OpenAI-Agent have shared their perspectives:\n\n{payload}\n\n"
                "How do you synthesize these perspectives from your role as AI companion and coordinator?"
            ),
            formatted_history=f"[User Probe]: {state.probe_prompt}",
        )
        latency = time.perf_counter() - t0

        if not gen_reply or not gen_reply.strip():
            state.status = "FAILED"
            state.error_message = "Genesis returned an empty or whitespace-only reply."
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        if gen_meta.get("is_truncated") or gen_meta.get("finish_reason") == "length":
            state.status = "FAILED"
            state.error_message = f"Genesis response truncated by output token limit: '{gen_reply[-60:]}...'"
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        cost = gen_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost

        turn_3 = {
            "turn_id": 3,
            "speaker": "Genesis",
            "content": gen_reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(gen_reply)) // 4,
            "model": gen_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            "metadata": gen_meta,
        }
        state.turns.append(turn_3)

        state.status = "COMPLETED"
        state.pending_gate = None
        self._save_triad_audit(state)

    # -------------------------------------------------------------------------
    # Persistence Helpers
    # -------------------------------------------------------------------------

    def _append_conversation_record(self, record: Dict[str, Any]) -> None:
        """Safely appends a conversation record to local history."""
        records = []
        if self.conversations_file.exists():
            try:
                records = json.loads(self.conversations_file.read_text(encoding="utf-8"))
            except Exception:
                records = []
        records.append(record)
        tmp_file = self.conversations_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
        tmp_file.replace(self.conversations_file)

    def _save_triad_audit(self, state: TriadSessionState) -> Path:
        """Persists the triad session record to logs/interface/triad_{session_id}.json."""
        audit_file = self.log_dir / f"triad_{state.session_id}.json"
        data = {
            "session_id": state.session_id,
            "start_time": state.start_time,
            "status": state.status,
            "is_live": state.is_live,
            "probe_prompt": state.probe_prompt,
            "turns": state.turns,
            "cumulative_cost_usd": round(state.cumulative_cost_usd, 6),
            "error_message": state.error_message,
        }
        tmp_file = audit_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_file.replace(audit_file)
        return audit_file
