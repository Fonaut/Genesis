"""
Genesis Communication Interface — Backend Orchestrator Engine
Bridges local web endpoints with existing Communication Hub participants,
approval gates, safety governor, and the Development Journal.
"""

import json
import os
import re
import threading
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
from memory.collaborative import (
    CollaborativeMemoryManager,
    MemoryCategory,
    ApprovalStatus,
    AuthorizationScope,
    MemoryProvenance,
)


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
        self.cost_ledger_file = self.log_dir / "cost_ledger.json"
        self.max_cost_usd = max_cost_usd
        self._cost_lock = threading.Lock()

        # Core safety governor and journal manager
        self.governor = SafetyGovernor(max_turns=20, max_cost_usd=max_cost_usd)
        self.journal_manager = DevelopmentJournalManager()
        self.sandbox = SandboxManager()
        self.collaborative_manager = CollaborativeMemoryManager()

        # Reconcile historical cumulative spending across all logs
        initial_reconciled_cost = self._reconcile_cumulative_costs()
        self.governor.cumulative_cost_usd = initial_reconciled_cost

        # In-memory active triad session state
        self.active_triad_session: Optional[TriadSessionState] = None

    # -------------------------------------------------------------------------
    # Cost Accounting & Spending Ledger
    # -------------------------------------------------------------------------

    def _reconcile_cumulative_costs(self) -> float:
        """
        Scans conversations.json and all triad_*.json log files to reconcile
        the true cumulative historical spending since server initialization.
        Populates cost_ledger.json with historical transactions if not already present.
        """
        if self.cost_ledger_file.exists():
            try:
                ledger = json.loads(self.cost_ledger_file.read_text(encoding="utf-8"))
                total = sum(float(e.get("cost_usd", 0.0)) for e in ledger)
                return round(total, 6)
            except Exception:
                pass

        # Reconstruct ledger from historical logs
        raw_events: List[Dict[str, Any]] = []

        if self.conversations_file.exists():
            try:
                convs = json.loads(self.conversations_file.read_text(encoding="utf-8"))
                for c in convs:
                    cost = float(c.get("cost_usd_est", 0.0))
                    is_live = bool(c.get("metadata", {}).get("is_live", False))
                    if cost > 0 or is_live:
                        spk = c.get("speaker", "unknown")
                        provider = "openai_metered" if spk in ("Genesis", "OpenAI-Agent") else "antigravity_subscription"
                        raw_events.append({
                            "timestamp": c.get("timestamp", ""),
                            "cost_usd": round(cost, 6),
                            "model": c.get("model", "unknown"),
                            "provider": provider,
                            "source": f"individual_chat_{spk}",
                            "session_id": None,
                            "tokens": c.get("metadata", {}),
                        })
            except Exception:
                pass

        for t_file in sorted(self.log_dir.glob("triad_*.json")):
            try:
                t_data = json.loads(t_file.read_text(encoding="utf-8"))
                sid = t_data.get("session_id")
                is_live = bool(t_data.get("is_live", False))
                for turn in t_data.get("turns", []):
                    cost = float(turn.get("cost_usd_est", 0.0))
                    if cost > 0 or is_live:
                        spk = turn.get("speaker", "unknown")
                        provider = "openai_metered" if spk in ("Genesis", "OpenAI-Agent") else "antigravity_subscription"
                        raw_events.append({
                            "timestamp": turn.get("timestamp", ""),
                            "cost_usd": round(cost, 6),
                            "model": turn.get("model", "unknown"),
                            "provider": provider,
                            "source": f"triad_turn_{turn.get('turn_id')}",
                            "session_id": sid,
                            "tokens": turn.get("metadata", {}),
                        })
            except Exception:
                pass

        # Sort chronologically
        raw_events.sort(key=lambda x: x.get("timestamp", ""))

        running_total = 0.0
        for ev in raw_events:
            running_total += ev["cost_usd"]
            ev["cumulative_cost_usd"] = round(running_total, 6)

        try:
            self.cost_ledger_file.write_text(json.dumps(raw_events, indent=2), encoding="utf-8")
        except Exception:
            pass

        return round(running_total, 6)

    def record_api_spending(
        self,
        cost_usd: float,
        model: str,
        provider: str,
        source: str,
        session_id: Optional[str] = None,
        tokens_info: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Thread-safe, atomic accounting method for tracking cumulative API spending
        across both individual chat dispatches and multi-agent triad turns.
        Enforces the $0.05 hard ceiling and distinguishes metered API billing from
        subscription tool usage.
        """
        with self._cost_lock:
            cost_float = float(cost_usd or 0.0)
            if cost_float > 0:
                prospective_total = self.governor.cumulative_cost_usd + cost_float
                if prospective_total > self.governor.max_cost_usd:
                    raise CostLimitExceededError(
                        f"Hard spending ceiling exceeded: attempted ${cost_float:.6f} USD would push "
                        f"cumulative spending to ${prospective_total:.6f} USD (hard ceiling: ${self.governor.max_cost_usd:.5f} USD). "
                        "Execution halted immediately."
                    )
                self.governor.cumulative_cost_usd = round(prospective_total, 6)

            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cost_usd": round(cost_float, 6),
                "cumulative_cost_usd": round(self.governor.cumulative_cost_usd, 6),
                "model": model,
                "provider": provider,  # "openai_metered" vs "antigravity_subscription"
                "source": source,      # "individual_chat", "triad_turn_1", etc.
                "session_id": session_id,
                "tokens": tokens_info or {},
            }
            self._append_cost_ledger_entry(entry)
            return self.governor.cumulative_cost_usd

    def _append_cost_ledger_entry(self, entry: Dict[str, Any]) -> None:
        """Appends a transaction entry to the persistent cost ledger."""
        ledger = []
        if self.cost_ledger_file.exists():
            try:
                ledger = json.loads(self.cost_ledger_file.read_text(encoding="utf-8"))
            except Exception:
                ledger = []
        ledger.append(entry)
        tmp_file = self.cost_ledger_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        tmp_file.replace(self.cost_ledger_file)

    def get_metered_cost_usd(self) -> float:
        """Returns total metered OpenAI API spending tracked in the ledger."""
        return round(self.governor.cumulative_cost_usd, 6)

    # -------------------------------------------------------------------------
    # Anti-Spoofing & Role Boundary Enforcement
    # -------------------------------------------------------------------------

    def format_participant_prompt(self, speaker: str, prompt: str) -> str:
        """
        Frames an incoming prompt with strict execution directives so the model
        answers only for its own assigned persona and does not fabricate other turns.
        """
        roles = {
            "Antigravity": "Technical Co-Architect & Execution Engine",
            "OpenAI-Agent": "Analytical Specialist & Critical Challenger",
            "Genesis": "Voice Companion & Orchestrator",
        }
        all_participants = ["Antigravity", "OpenAI-Agent", "Genesis"]
        other_participants = [p for p in all_participants if p.lower() != speaker.lower()]
        role = roles.get(speaker, "Collaborator")

        return (
            f"[SYSTEM DIRECTIVE — MULTI-AGENT EXECUTION BOUNDARY]\n"
            f"You are speaking strictly as: {speaker} (Role: {role}).\n"
            f"- Provide ONLY your own individual perspective and response.\n"
            f"- DO NOT simulate, impersonate, quote, or generate dialogue on behalf of other participants ({', '.join(other_participants)}).\n"
            f"- Subsequent turns will be executed by those agents independently. Any content you generate claiming to represent other agents will be rejected by the Hub.\n"
            f"[END DIRECTIVE]\n\n"
            f"{prompt}"
        )

    def sanitize_agent_turn_output(self, speaker: str, text: str) -> Tuple[str, List[str]]:
        """
        Sanitizes raw model output to prevent agent role spoofing / response contamination.
        If a model attempts to generate headers or dialogue for other participants,
        the Hub cuts off the unauthorized trailing sections and returns only the
        authoritative contribution of the current speaker.
        """
        if not text:
            return "", []

        participants = ["Antigravity", "OpenAI-Agent", "Genesis"]
        other_participants = [p for p in participants if p.lower() != speaker.lower()]

        lines = text.split("\n")
        cleaned_lines = []
        spoofed_found = []

        def is_participant_header(line: str, name: str) -> bool:
            norm = line.strip().lower()
            name_lower = name.lower()
            if name_lower not in norm:
                return False
            stripped = re.sub(r"^[#\s*\-_>\[\]]+", "", norm)
            stripped = re.sub(r"[\s*\-_:\]]+$", "", stripped)
            return stripped.startswith(name_lower)

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            found_other = None
            for other in other_participants:
                if is_participant_header(line_stripped, other):
                    found_other = other
                    break

            if found_other:
                spoofed_found.append(found_other)
                # Check remaining lines for any additional spoofed participants
                for rem_line in lines[i + 1:]:
                    for other in other_participants:
                        if is_participant_header(rem_line.strip(), other) and other not in spoofed_found:
                            spoofed_found.append(other)
                break

            cleaned_lines.append(line)

        # Pop trailing horizontal rules and whitespace
        while cleaned_lines and (re.match(r"^[-*_]{3,}$", cleaned_lines[-1].strip()) or not cleaned_lines[-1].strip()):
            cleaned_lines.pop()

        # Strip redundant leading self-header if present (e.g. ### **Antigravity (Proposal)**)
        while cleaned_lines and (is_participant_header(cleaned_lines[0], speaker) or not cleaned_lines[0].strip()):
            cleaned_lines.pop(0)

        result_text = "\n".join(cleaned_lines).strip()
        return result_text, sorted(list(set(spoofed_found)))

    # -------------------------------------------------------------------------
    # Kill Switch Controls
    # -------------------------------------------------------------------------

    @property
    def is_kill_switch_active(self) -> bool:
        return self.governor.is_kill_switch_active

    def trigger_kill_switch(self) -> Dict[str, Any]:
        """Activates emergency stop immediately, blocking all outbound traffic."""
        self.governor.stop_file_path.touch()

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
        if self.governor.stop_file_path.exists():
            self.governor.stop_file_path.unlink()
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

        # Record and verify spending cap via unified thread-safe ledger
        provider = "openai_metered" if recipient in ("Genesis", "OpenAI-Agent") else "antigravity_subscription"
        self.record_api_spending(
            cost_usd=cost_est,
            model=model_name,
            provider=provider,
            source=f"individual_chat_{recipient}",
            tokens_info=turn_meta,
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
        if not state:
            raise HubSafetyError("No active triad session.")

        if state.status in ("CANCELLED_BY_USER", "COMPLETED", "FAILED"):
            raise HubSafetyError(
                f"Triad session '{state.session_id}' is already in terminal state '{state.status}'. "
                "Cannot respond to gates or dispatch further turns."
            )

        if not state.pending_gate:
            raise HubSafetyError("No pending approval gate for active triad session.")

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
            audit_file = self._save_triad_audit(state)

            # Record historical rejected proposal (isolated from active recommendations)
            try:
                snippet = gate.proposed_payload[:120].replace("\n", " ")
                self.collaborative_manager.propose_memory(
                    content=f"Proposed action '{gate.action_name}' with payload '{snippet}...' was rejected by Fodoh.",
                    speaker=gate.recipient,
                    category=MemoryCategory.UNVERIFIED_PROPOSAL.value,
                    session_id=state.session_id,
                    transcript_path=str(audit_file),
                    authorization_record=f"{gate.approval_id}:REJECTED_BY_FODOH",
                    rationale="Human gate rejection: Action explicitly rejected by Fodoh.",
                )
            except Exception:
                pass

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

        # Apply strict multi-agent boundary directive to prevent role hallucination
        framed_prompt = self.format_participant_prompt("Antigravity", payload)
        ag_response, ag_thought, ag_meta = agent.send_probe(
            prompt=framed_prompt,
            sandbox=self.sandbox,
        )
        latency = time.perf_counter() - t0

        if not ag_response or not ag_response.strip():
            state.status = "FAILED"
            state.error_message = "Antigravity returned an empty or whitespace-only reply."
            state.pending_gate = None
            self._save_triad_audit(state)
            return

        # Sanitize agent response to detect and cut off any spoofed sections
        clean_ag_response, spoofed_agents = self.sanitize_agent_turn_output("Antigravity", ag_response)
        if spoofed_agents:
            ag_meta["spoofed_sections_detected"] = spoofed_agents
            ag_meta["sanitized"] = True

        cost = ag_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost
        self.record_api_spending(
            cost_usd=cost,
            model=ag_meta.get("model", "gemini-3.8-flash-high" if state.is_live else "mock-synthetic"),
            provider="antigravity_subscription",
            source="triad_turn_1",
            session_id=state.session_id,
            tokens_info=ag_meta.get("usage", {}),
        )

        turn_1 = {
            "turn_id": 1,
            "speaker": "Antigravity",
            "content": clean_ag_response,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(clean_ag_response)) // 4,
            "model": ag_meta.get("model", "gemini-3.8-flash-high" if state.is_live else "mock-synthetic"),
            "metadata": ag_meta,
        }
        state.turns.append(turn_1)

        # Prepare Gate 2 with Authoritative Hub Framing
        hub_framed_payload = (
            "============================================================\n"
            "AUTHORITATIVE TURN 1: [Antigravity] (Technical Co-Architect)\n"
            "============================================================\n"
            f"{clean_ag_response}"
        )
        next_gate = PendingApprovalGate(
            approval_id=f"gate_2_{state.session_id}",
            action_name="Pass Antigravity Response to OpenAI-Agent",
            recipient="OpenAI-Agent",
            proposed_payload=hub_framed_payload,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state.pending_gate = next_gate
        state.status = "PENDING_APPROVAL"

    def _execute_triad_turn_2(self, state: TriadSessionState, payload: str) -> None:
        """Executes Turn 2 (OpenAI-Agent) and sets up Gate 3."""
        t0 = time.perf_counter()
        agent = OpenAIAgentParticipant(is_live=state.is_live, max_tokens=800)
        framed_payload = self.format_participant_prompt("OpenAI-Agent", payload)
        openai_reply, openai_meta = agent.process_turn(
            speaker="Antigravity",
            incoming_message=framed_payload,
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

        # Sanitize agent response to detect and cut off any spoofed sections
        clean_openai_reply, spoofed_agents = self.sanitize_agent_turn_output("OpenAI-Agent", openai_reply)
        if spoofed_agents:
            openai_meta["spoofed_sections_detected"] = spoofed_agents
            openai_meta["sanitized"] = True

        cost = openai_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost
        self.record_api_spending(
            cost_usd=cost,
            model=openai_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            provider="openai_metered",
            source="triad_turn_2",
            session_id=state.session_id,
            tokens_info=openai_meta,
        )

        turn_2 = {
            "turn_id": 2,
            "speaker": "OpenAI-Agent",
            "content": clean_openai_reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(clean_openai_reply)) // 4,
            "model": openai_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            "metadata": openai_meta,
        }
        state.turns.append(turn_2)

        ag_response = state.turns[0]["content"]
        multi_agent_payload = (
            "============================================================\n"
            "AUTHORITATIVE TURN 1: [Antigravity] (Technical Co-Architect)\n"
            "============================================================\n"
            f"{ag_response}\n\n"
            "============================================================\n"
            "AUTHORITATIVE TURN 2: [OpenAI-Agent] (Analytical Specialist)\n"
            "============================================================\n"
            f"{clean_openai_reply}"
        )

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
        genesis_incoming = (
            f"Antigravity and OpenAI-Agent have shared their perspectives:\n\n{payload}\n\n"
            "Synthesize both perspectives from your role as AI companion and coordinator."
        )
        framed_genesis = self.format_participant_prompt("Genesis", genesis_incoming)
        gen_reply, gen_thought, gen_meta = agent.process_turn(
            speaker="OpenAI-Agent",
            incoming_message=framed_genesis,
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

        clean_gen_reply, spoofed_agents = self.sanitize_agent_turn_output("Genesis", gen_reply)
        if spoofed_agents:
            gen_meta["spoofed_sections_detected"] = spoofed_agents
            gen_meta["sanitized"] = True

        cost = gen_meta.get("cost_usd_est", 0.0)
        state.cumulative_cost_usd += cost
        self.record_api_spending(
            cost_usd=cost,
            model=gen_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            provider="openai_metered",
            source="triad_turn_3",
            session_id=state.session_id,
            tokens_info=gen_meta,
        )

        turn_3 = {
            "turn_id": 3,
            "speaker": "Genesis",
            "content": clean_gen_reply,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(cost, 6),
            "tokens_estimated": (len(payload) + len(clean_gen_reply)) // 4,
            "model": gen_meta.get("model", "gpt-4o-mini" if state.is_live else "synthetic-offline"),
            "metadata": gen_meta,
        }
        state.turns.append(turn_3)

        state.status = "COMPLETED"
        state.pending_gate = None
        audit_file = self._save_triad_audit(state)

        # Stage candidate collaborative synthesis memory into review queue as UNVERIFIED_PROPOSAL
        # (Separating communication transit gate from long-term memory approval)
        try:
            prompt_snip = state.probe_prompt[:80].replace("\n", " ")
            self.collaborative_manager.propose_memory(
                content=f"Triad discussion synthesis: Genesis synthesized Antigravity and OpenAI-Agent inputs on '{prompt_snip}'.",
                speaker="Genesis",
                category=MemoryCategory.UNVERIFIED_PROPOSAL.value,
                session_id=state.session_id,
                turn_id=3,
                transcript_path=str(audit_file),
                authorization_record=f"comm_transit_gate_3_{state.session_id}:APPROVED_BY_FODOH",
                authorization_scope=AuthorizationScope.COMMUNICATION_TRANSIT.value,
                is_synthetic=(not state.is_live),
                rationale="Supervised triad communication round completed. Synthesis generated by Genesis; pending explicit human memory review for consensus/decision approval.",
            )
        except Exception:
            pass

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
