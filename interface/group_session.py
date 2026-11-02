"""
Genesis Communication Interface — Multi-Round Group Collaboration Engine (Phase 2)
Provides persistent, crash-recoverable group discussion sessions where Fodoh,
Genesis, Antigravity, and OpenAI-Agent collaborate across flexible rounds.
"""

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from hub.phase0 import PROJECT_ROOT, HubSafetyError
from interface.models import GroupSessionState, GroupTurnRecord


class MultiRoundSessionManager:
    """
    Manages persistent, crash-resilient multi-agent group conversation sessions.
    Guarantees:
    - Atomic disk writes (no half-written transcripts).
    - Thread-safe synchronization.
    - Recovery of in-flight turns to UNKNOWN_OUTCOME without auto-replay.
    - Nonce-based gate invalidation on restart.
    - Zero duplicate turn insertions via idempotency tokens.
    """

    def __init__(self, log_dir: Optional[Path] = None, engine: Optional[Any] = None):
        self.log_dir = log_dir or (PROJECT_ROOT / "logs" / "interface")
        self.sessions_dir = self.log_dir / "group_sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.engine = engine
        self._lock = threading.Lock()
        self.active_session_id: Optional[str] = None

    def _get_session_path(self, session_id: str) -> Path:
        # Sanitize session_id to avoid path traversal
        if not session_id or not isinstance(session_id, str):
            raise HubSafetyError("Session ID must be a non-empty string.")
        if "/" in session_id or "\\" in session_id or ".." in session_id:
            raise HubSafetyError(f"Invalid session ID format: '{session_id}'. Path traversal rejected.")
        clean_id = Path(session_id).name
        return self.sessions_dir / f"session_{clean_id}.json"

    def create_session(
        self,
        title: str = "Collaborative Topic",
        is_live: bool = False,
        max_turns: int = 10,
        initial_prompt: Optional[str] = None,
    ) -> GroupSessionState:
        """
        Initializes a new persistent multi-round group session.
        If initial_prompt is provided, records Fodoh's opening statement as Turn 0 ($0 cost).
        """
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            sid = f"group_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

            turns: List[GroupTurnRecord] = []
            if initial_prompt and initial_prompt.strip():
                clean_p = initial_prompt.strip()
                t0 = GroupTurnRecord(
                    turn_id=f"turn_0_{uuid.uuid4().hex[:6]}",
                    turn_index=0,
                    speaker="Fodoh",
                    speaker_type="HUMAN",
                    content=clean_p,
                    timestamp=now_iso,
                    model="human",
                    cost_usd_est=0.0,
                    latency_sec=0.0,
                    idempotency_token=f"init_{sid}_{uuid.uuid4().hex[:6]}",
                    status="COMMITTED",
                    metadata={"source": "session_init_prompt"},
                )
                turns.append(t0)

            session = GroupSessionState(
                session_id=sid,
                title=title.strip() or "Collaborative Topic",
                created_at=now_iso,
                updated_at=now_iso,
                status="WAITING_FOR_HUMAN",
                is_live=is_live,
                max_turns=max_turns,
                turns=turns,
                active_gate=None,
                in_flight_reservation_id=None,
                cumulative_cost_usd=0.0,
                recommended_next_speaker=None,
                error_message=None,
            )

            self._save_session_atomic(session)
            self.active_session_id = sid
            return session

    def get_session(self, session_id: str) -> Optional[GroupSessionState]:
        """Reads a session state by ID from disk."""
        path = self._get_session_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return GroupSessionState(**data)
        except Exception:
            return None

    def get_active_session(self) -> Optional[GroupSessionState]:
        """Returns the currently active session state, or the most recent non-completed session."""
        if self.active_session_id:
            sess = self.get_session(self.active_session_id)
            if sess and sess.status not in ("COMPLETED", "CANCELLED"):
                return sess

        # Fallback to most recently updated active/paused session
        all_sessions = self.list_sessions()
        for s in all_sessions:
            if s.get("status") in ("WAITING_FOR_HUMAN", "DISPATCH_PENDING", "EXECUTING_TURN", "UNKNOWN_OUTCOME", "PAUSED"):
                self.active_session_id = s["session_id"]
                return self.get_session(s["session_id"])
        return None

    def _save_session_atomic(self, session: GroupSessionState) -> None:
        """Atomically persists session state to disk using a temporary file and atomic rename."""
        target_path = self._get_session_path(session.session_id)
        tmp_path = target_path.with_suffix(".tmp")
        data = session.model_dump()
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(target_path)

    def append_turn(self, session_id: str, turn: GroupTurnRecord) -> GroupSessionState:
        """
        Appends a committed turn to the session transcript under strict idempotency.
        Rejects duplicate idempotency tokens and out-of-order sequence indices.
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")

            # Idempotency check: verify token has not already been committed
            for existing in session.turns:
                if existing.idempotency_token == turn.idempotency_token:
                    raise ValueError(f"Duplicate turn rejected: idempotency token '{turn.idempotency_token}' already committed.")

            # Sequence verification
            expected_index = len(session.turns)
            if turn.turn_index != expected_index:
                raise ValueError(f"Turn index mismatch: expected {expected_index}, got {turn.turn_index}.")

            session.turns.append(turn)
            session.updated_at = datetime.now(timezone.utc).isoformat()
            session.cumulative_cost_usd = round(session.cumulative_cost_usd + turn.cost_usd_est, 6)

            # Check if session turn limit has been reached
            if len(session.turns) >= session.max_turns:
                session.status = "COMPLETED"
                session.error_message = f"Session completed: reached bounded turn limit ({session.max_turns})."
            else:
                session.status = "WAITING_FOR_HUMAN"

            self._save_session_atomic(session)
            return session

    def pause_session(self, session_id: str) -> GroupSessionState:
        """Suspends an active session cleanly."""
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status in ("COMPLETED", "CANCELLED"):
                raise HubSafetyError(f"Cannot pause session in terminal status '{session.status}'.")

            session.status = "PAUSED"
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_session_atomic(session)
            return session

    def resume_session(self, session_id: str) -> GroupSessionState:
        """Resumes a paused or recoverable session."""
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status not in ("PAUSED", "UNKNOWN_OUTCOME", "FAILED"):
                raise HubSafetyError(f"Cannot resume session with status '{session.status}'.")

            session.status = "WAITING_FOR_HUMAN"
            session.error_message = None
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_session_atomic(session)
            self.active_session_id = session.session_id
            return session

    def terminate_session(self, session_id: str, reason: str = "Completed by user") -> GroupSessionState:
        """Ends a session cleanly."""
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")

            session.status = "COMPLETED"
            session.active_gate = None
            session.updated_at = datetime.now(timezone.utc).isoformat()
            session.error_message = reason
            self._save_session_atomic(session)
            if self.active_session_id == session_id:
                self.active_session_id = None
            return session

    def recover_sessions_on_startup(self) -> int:
        """
        Crash Recovery Routine executed upon server boot:
        1. Identifies any sessions left in 'EXECUTING_TURN'.
           - Marks them as 'UNKNOWN_OUTCOME'.
           - Releases any orphaned budget reservation in the engine.
           - Requires explicit human authorization before retrying.
        2. Identifies any sessions left in 'DISPATCH_PENDING'.
           - Invalidates the active approval gate (gate nonce expired).
           - Returns state to 'WAITING_FOR_HUMAN'.
        Returns the total number of sessions safely recovered.
        """
        with self._lock:
            recovered_count = 0
            for s_file in self.sessions_dir.glob("session_*.json"):
                try:
                    data = json.loads(s_file.read_text(encoding="utf-8"))
                    session = GroupSessionState(**data)
                    modified = False

                    if session.status == "EXECUTING_TURN":
                        session.status = "UNKNOWN_OUTCOME"
                        session.error_message = (
                            "Server was interrupted or restarted while an agent turn was in-flight. "
                            "Turn outcome is uncertain. Previous turns are preserved. "
                            "Explicit human instruction required to retry or proceed."
                        )
                        # Release orphaned in-flight reservation if engine is connected
                        if session.in_flight_reservation_id and self.engine:
                            try:
                                self.engine.release_api_budget(session.in_flight_reservation_id)
                            except Exception:
                                pass
                        session.in_flight_reservation_id = None
                        session.updated_at = datetime.now(timezone.utc).isoformat()
                        modified = True
                        recovered_count += 1

                    elif session.status == "DISPATCH_PENDING":
                        # Stale gate invalidation
                        session.status = "WAITING_FOR_HUMAN"
                        session.active_gate = None
                        session.error_message = "Pending approval gate was invalidated due to server restart. Please prepare a new turn."
                        session.updated_at = datetime.now(timezone.utc).isoformat()
                        modified = True
                        recovered_count += 1

                    if modified:
                        self._save_session_atomic(session)
                except Exception:
                    pass

            return recovered_count

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists all group sessions sorted by last updated timestamp descending."""
        sessions = []
        for s_file in self.sessions_dir.glob("session_*.json"):
            try:
                data = json.loads(s_file.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id"),
                    "title": data.get("title"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "status": data.get("status"),
                    "is_live": data.get("is_live", False),
                    "turns_count": len(data.get("turns", [])),
                    "cumulative_cost_usd": data.get("cumulative_cost_usd", 0.0),
                    "recommended_next_speaker": data.get("recommended_next_speaker"),
                })
            except Exception:
                pass

        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    # -------------------------------------------------------------------------
    # Milestone B: Human Participation & Staged Agent Dispatches
    # -------------------------------------------------------------------------

    def interject_human_message(
        self,
        session_id: str,
        message: str,
        recommended_next_speaker: Optional[str] = None,
    ) -> GroupSessionState:
        """
        Allows Fodoh to participate directly in the group discussion.
        Human-only turns incur $0 cost and dispatch zero model requests.
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status in ("COMPLETED", "CANCELLED"):
                raise HubSafetyError(f"Cannot interject in terminal session status '{session.status}'.")

            clean_msg = message.strip()
            if not clean_msg:
                raise ValueError("Interjection message cannot be empty.")

            now_iso = datetime.now(timezone.utc).isoformat()
            turn = GroupTurnRecord(
                turn_id=f"turn_{len(session.turns)}_{uuid.uuid4().hex[:6]}",
                turn_index=len(session.turns),
                speaker="Fodoh",
                speaker_type="HUMAN",
                content=clean_msg,
                timestamp=now_iso,
                model="human",
                cost_usd_est=0.0,
                latency_sec=0.0,
                idempotency_token=f"human_{session_id}_{len(session.turns)}_{uuid.uuid4().hex[:6]}",
                status="COMMITTED",
                metadata={"source": "human_interjection"},
            )

            session.turns.append(turn)
            session.updated_at = now_iso
            session.status = "WAITING_FOR_HUMAN"
            session.error_message = None
            if recommended_next_speaker:
                session.recommended_next_speaker = recommended_next_speaker

            # Check bounded turn limit
            if len(session.turns) >= session.max_turns:
                session.status = "COMPLETED"
                session.error_message = f"Session completed: reached bounded turn limit ({session.max_turns})."

            self._save_session_atomic(session)
            return session

    def format_session_history_context(self, session: GroupSessionState, max_recent_turns: int = 8) -> str:
        """Constructs a sliding-window context transcript for target agent prompt framing."""
        recent_turns = session.turns[-max_recent_turns:] if len(session.turns) > max_recent_turns else session.turns
        lines = []
        for t in recent_turns:
            spk_label = f"[{t.speaker}]" if t.speaker_type == "HUMAN" else f"[{t.speaker} (Agent)]"
            lines.append(f"{spk_label}:\n{t.content}\n")
        return "\n".join(lines).strip()

    def prepare_agent_turn(
        self,
        session_id: str,
        target_agent: str,
        prompt_override: Optional[str] = None,
    ) -> Tuple[GroupSessionState, Dict[str, Any]]:
        """
        Stages an agent turn and sets up an explicit approval gate with nonce.
        Does NOT dispatch or incur cost until Fodoh approves.
        """
        valid_agents = ["Antigravity", "OpenAI-Agent", "Genesis"]
        if target_agent not in valid_agents:
            raise ValueError(f"Invalid target agent '{target_agent}'. Must be one of {valid_agents}.")

        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status != "WAITING_FOR_HUMAN":
                raise HubSafetyError(
                    f"Cannot prepare turn: session '{session_id}' is in status '{session.status}'. "
                    "Must be in 'WAITING_FOR_HUMAN'."
                )
            if len(session.turns) >= session.max_turns:
                raise HubSafetyError(f"Session has reached its bounded turn limit ({session.max_turns}).")

            history_ctx = self.format_session_history_context(session)
            instruction = prompt_override.strip() if prompt_override and prompt_override.strip() else (
                f"Review the group conversation history above and provide your independent perspective as {target_agent}."
            )

            proposed_payload = (
                f"=== GROUP CONVERSATION TRANSCRIPT ===\n"
                f"{history_ctx}\n\n"
                f"=== INSTRUCTION FOR {target_agent.upper()} ===\n"
                f"{instruction}"
            )

            gate_nonce = f"gate_{session_id}_{len(session.turns)}_{uuid.uuid4().hex[:8]}"
            gate = {
                "gate_nonce": gate_nonce,
                "target_agent": target_agent,
                "proposed_payload": proposed_payload,
                "prompt_override": prompt_override,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "PENDING",
            }

            session.active_gate = gate
            session.status = "DISPATCH_PENDING"
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_session_atomic(session)
            return session, gate

    def dispatch_agent_turn(
        self,
        session_id: str,
        gate_nonce: str,
        action: str,
        edited_payload: Optional[str] = None,
    ) -> GroupSessionState:
        """
        Executes or cancels a staged turn gate upon human approval.
        Enforces pre-dispatch atomic budget reservations, anti-spoofing, and crash logging.
        """
        if self.engine and self.engine.is_kill_switch_active:
            raise HubSafetyError("Operation aborted: Emergency kill switch is active.")

        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status != "DISPATCH_PENDING":
                raise HubSafetyError(f"Cannot dispatch turn: session status is '{session.status}'. Expected 'DISPATCH_PENDING'.")
            if not session.active_gate or session.active_gate.get("gate_nonce") != gate_nonce:
                raise HubSafetyError("Invalid or expired gate nonce. Approval rejected.")

            target_agent = session.active_gate["target_agent"]

            # Case A: Human Rejection
            if action.lower() == "reject":
                session.active_gate = None
                session.status = "WAITING_FOR_HUMAN"
                session.error_message = f"Turn dispatch for {target_agent} was cancelled by Fodoh."
                session.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_session_atomic(session)
                return session

            if action.lower() != "approve":
                raise ValueError(f"Unknown action '{action}'. Must be 'approve' or 'reject'.")

            payload = (edited_payload.strip() if edited_payload and edited_payload.strip() else session.active_gate["proposed_payload"])

            # Pre-dispatch budget reservation
            est_cost = 0.0
            res_id = None
            if self.engine:
                est_cost = self.engine.estimate_pre_dispatch_cost(target_agent, session.is_live, payload, max_tokens=800)
                res_id = self.engine.reserve_api_budget(
                    estimated_cost_usd=est_cost,
                    source=f"group_session_{session_id}_{target_agent}",
                    session_id=session_id,
                )

            # Mark EXECUTING_TURN on disk before dispatch (for crash recovery)
            session.status = "EXECUTING_TURN"
            session.in_flight_reservation_id = res_id
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_session_atomic(session)

        # Execute agent turn outside session lock
        t0 = time.perf_counter()
        reply_content = ""
        model_name = "mock"
        turn_meta: Dict[str, Any] = {}
        cost_est = 0.0
        provider = "openai_metered" if target_agent in ("Genesis", "OpenAI-Agent") else "antigravity_subscription"

        try:
            from hub.phase0 import CliAntigravityAgent, MockAntigravityAgent, GenesisParticipant
            from hub.phase1 import OpenAIAgentParticipant

            # Frame prompt with boundary directive
            framed_payload = payload
            if self.engine:
                framed_payload = self.engine.format_participant_prompt(target_agent, payload)

            if target_agent == "Antigravity":
                if session.is_live:
                    agent = CliAntigravityAgent(timeout_sec=60.0)
                else:
                    agent = MockAntigravityAgent(
                        response_text=f"Antigravity technical assessment: Group coordination verified for session {session_id}."
                    )
                sandbox = self.engine.sandbox if self.engine else None
                ag_text, ag_thought, ag_meta = agent.send_probe(prompt=framed_payload, sandbox=sandbox)
                reply_content = ag_text
                turn_meta = ag_meta
                model_name = ag_meta.get("model", "gemini-3.8-flash-high" if session.is_live else "mock-synthetic")
                cost_est = ag_meta.get("cost_usd_est", 0.0)

            elif target_agent == "Genesis":
                agent = GenesisParticipant(is_live=session.is_live, max_tokens=800)
                gen_reply, gen_thought, gen_meta = agent.process_turn(
                    speaker="Fodoh",
                    incoming_message=framed_payload,
                    formatted_history=f"[Session {session_id}]",
                )
                reply_content = gen_reply
                turn_meta = gen_meta
                model_name = gen_meta.get("model", "gpt-4o-mini" if session.is_live else "synthetic-offline")
                cost_est = gen_meta.get("cost_usd_est", 0.0)

            elif target_agent == "OpenAI-Agent":
                agent = OpenAIAgentParticipant(is_live=session.is_live, max_tokens=800)
                oa_reply, oa_meta = agent.process_turn(
                    speaker="Fodoh",
                    incoming_message=framed_payload,
                    formatted_history=f"[Session {session_id}]",
                )
                reply_content = oa_reply
                turn_meta = oa_meta
                model_name = oa_meta.get("model", "gpt-4o-mini" if session.is_live else "synthetic-offline")
                cost_est = oa_meta.get("cost_usd_est", 0.0)

            latency = time.perf_counter() - t0

            if not reply_content or not reply_content.strip():
                raise HubSafetyError(f"{target_agent} returned an empty or whitespace-only response.")

            # Sanitize response
            clean_reply = reply_content
            if self.engine:
                clean_reply, detected_spoofs = self.engine.sanitize_agent_turn_output(target_agent, reply_content)
                if detected_spoofs:
                    turn_meta["spoofed_sections_detected"] = detected_spoofs
                    turn_meta["sanitized"] = True

            # Settle budget reservation
            if self.engine and res_id:
                self.engine.settle_api_budget(
                    reservation_id=res_id,
                    actual_cost_usd=cost_est,
                    model=model_name,
                    provider=provider,
                    source=f"group_turn_{target_agent}",
                    session_id=session_id,
                    tokens_info=turn_meta,
                )

        except Exception as err:
            # Release reservation and mark session FAILED
            if self.engine and res_id:
                self.engine.release_api_budget(res_id)
            with self._lock:
                session = self.get_session(session_id)
                if session:
                    session.status = "FAILED"
                    session.in_flight_reservation_id = None
                    session.error_message = f"Turn execution failed for {target_agent}: {str(err)}"
                    session.updated_at = datetime.now(timezone.utc).isoformat()
                    self._save_session_atomic(session)
            raise

        # Commit completed turn
        with self._lock:
            session = self.get_session(session_id)
            now_iso = datetime.now(timezone.utc).isoformat()
            new_turn = GroupTurnRecord(
                turn_id=f"turn_{len(session.turns)}_{uuid.uuid4().hex[:6]}",
                turn_index=len(session.turns),
                speaker=target_agent,
                speaker_type="AGENT",
                content=clean_reply,
                timestamp=now_iso,
                model=model_name,
                cost_usd_est=round(cost_est, 6),
                latency_sec=round(latency, 3),
                idempotency_token=f"agent_{session_id}_{len(session.turns)}_{uuid.uuid4().hex[:6]}",
                status="COMMITTED",
                metadata=turn_meta,
            )
            session.turns.append(new_turn)
            session.cumulative_cost_usd = round(session.cumulative_cost_usd + cost_est, 6)
            session.active_gate = None
            session.in_flight_reservation_id = None
            session.updated_at = now_iso
            session.error_message = None

            if len(session.turns) >= session.max_turns:
                session.status = "COMPLETED"
                session.error_message = f"Session completed: reached bounded turn limit ({session.max_turns})."
            else:
                session.status = "WAITING_FOR_HUMAN"

            self._save_session_atomic(session)
            return session

    def recover_turn_action(self, session_id: str, action: str) -> GroupSessionState:
        """
        Allows Fodoh to resolve an UNKNOWN_OUTCOME or FAILED session:
        - 'mark_aborted': cleans diagnostic message and returns to WAITING_FOR_HUMAN.
        - 'retry': returns to WAITING_FOR_HUMAN so turn can be re-prepared.
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")
            if session.status not in ("UNKNOWN_OUTCOME", "FAILED"):
                raise HubSafetyError(f"Cannot recover session in status '{session.status}'. Must be UNKNOWN_OUTCOME or FAILED.")

            session.status = "WAITING_FOR_HUMAN"
            session.error_message = f"Session recovered by Fodoh (action: {action}). Previous turns preserved."
            session.in_flight_reservation_id = None
            session.active_gate = None
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_session_atomic(session)
            return session

    def extract_memories(
        self,
        session_id: str,
        stage_in_review_queue: bool = True,
    ) -> Dict[str, Any]:
        """
        Extracts structured candidate memories from a completed or active group session.
        If stage_in_review_queue is True and collaborative manager is available,
        proposes new candidates into the staging queue with strict deduplication.
        """
        with self._lock:
            session = self.get_session(session_id)
            if not session:
                raise HubSafetyError(f"Session '{session_id}' not found.")

        candidates = GroupMemoryExtractor.extract_candidates(session)
        staged_count = 0
        skipped_count = 0

        if stage_in_review_queue and self.engine and hasattr(self.engine, "collaborative_manager"):
            mgr = self.engine.collaborative_manager
            existing_items = mgr._read_staging_json()
            existing_keys = {
                (item.get("session_id"), item.get("turn_id"))
                for item in existing_items
                if item.get("session_id") is not None and item.get("turn_id") is not None
            }
            existing_contents = {
                item.get("content", "").strip()
                for item in existing_items
                if item.get("content")
            }

            for cand in candidates:
                key = (cand["session_id"], cand["turn_id"])
                clean_cand_content = cand["content"].strip()
                if key in existing_keys or clean_cand_content in existing_contents:
                    skipped_count += 1
                    continue

                mgr.propose_memory(
                    content=cand["content"],
                    speaker=cand["speaker"],
                    category=cand["category"],
                    session_id=cand["session_id"],
                    turn_id=cand["turn_id"],
                    transcript_path=str(self._get_session_path(session_id)),
                    authorization_record=cand["authorization_record"],
                    authorization_scope=cand["authorization_scope"],
                    is_synthetic=cand["is_synthetic"],
                    rationale=cand["rationale"],
                )
                existing_keys.add(key)
                existing_contents.add(clean_cand_content)
                staged_count += 1

        return {
            "session_id": session_id,
            "extraction_method": "deterministic_offline",
            "extracted_count": len(candidates),
            "staged_count": staged_count,
            "skipped_duplicates_count": skipped_count,
            "candidates": candidates,
        }


class GroupMemoryExtractor:
    """
    Deterministically extracts structured candidate memories from a multi-round group session.
    Features:
    - 100% deterministic local heuristics (Zero paid API calls).
    - Excludes routine test confirmations, boilerplate greetings, and low-information synthetic replies.
    - Accurately classifies:
      * HUMAN_DIRECTIVE (authentic prescriptive guidance from Fodoh; is_synthetic=False).
      * UNRESOLVED_QUESTION (open inquiry or research question from Fodoh; is_synthetic=False).
      * UNVERIFIED_PROPOSAL (substantive architectural recommendation or design option).
      * CRITICAL_CHALLENGE (substantive critique, trade-off analysis, or counter-perspective).
    - Preserves authoritative speaker attribution, source turn index, and transcript anchors.
    - Strictly prevents duplicate extraction within a session and against the staging store.
    - Tags candidate memories with COMMUNICATION_TRANSIT scope.
    - Never automatically approves or promotes memories into trusted long-term memory.
    """

    # Low-information and routine boilerplate patterns to filter out
    BOILERPLATE_PATTERNS = [
        "group coordination verified",
        "operating in offline dry-run mode",
        "companion pipeline and memory architecture are active and isolated",
        "test assessment",
        "ping",
        "pong",
        "verification check passed",
        "dry-run mode",
    ]

    # Conversational greetings to exclude when appearing as standalone short messages
    GREETING_STARTS = [
        "hello",
        "hi ",
        "hey ",
        "good morning",
        "good afternoon",
        "good evening",
        "thanks",
        "thank you",
        "ok",
        "okay",
    ]

    # Prescriptive directive indicators in human messages (authoritative guidance, decisions, constraints)
    DIRECTIVE_INDICATORS = [
        "want", "must", "will", "do not", "don't", "require",
        "prefer", "ensure", "always", "never", "need", "make the final",
        "preserve", "keep", "restrict", "allow", "disallow", "decision",
        "mandate", "instruct"
    ]

    @classmethod
    def is_low_information_turn(cls, content: str, speaker_type: str) -> bool:
        """Determines if a turn contains only low-information routine test confirmation or greeting."""
        clean = content.strip().lower()
        if len(clean) < 15:
            return True

        # Check for boilerplate confirmation patterns
        for pattern in cls.BOILERPLATE_PATTERNS:
            if pattern in clean and len(clean) < 150:
                return True

        # Check for pure greeting with no substantive body
        if any(clean.startswith(g) for g in cls.GREETING_STARTS) and len(clean) < 80:
            return True

        return False

    @classmethod
    def extract_candidates(cls, session: GroupSessionState) -> List[Dict[str, Any]]:
        candidates = []
        seen_turn_hashes = set()

        for turn in session.turns:
            content = turn.content.strip()
            if not content:
                continue

            # 1. Skip low-information routine confirmations, pings, and greetings
            if cls.is_low_information_turn(content, turn.speaker_type):
                continue

            # 2. In-session content deduplication (prevents multiple identical mock turns)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            if content_hash in seen_turn_hashes:
                continue
            seen_turn_hashes.add(content_hash)

            lower_content = content.lower()
            has_disagreement = any(
                w in lower_content for w in [
                    "disagree", "critique", "challenge", "caution", "risk",
                    "however", "flaw", "alternative", "counter-proposal"
                ]
            )
            has_directive = any(w in lower_content for w in cls.DIRECTIVE_INDICATORS)
            is_question = content.endswith("?") or any(
                lower_content.startswith(w) for w in ["how", "what", "why", "can we", "should we", "could we"]
            )

            # 3. Classify Category and Provenance
            if turn.speaker_type == "HUMAN":
                # Authentic human input is NEVER synthetic, even in offline mode
                is_synthetic = False

                if is_question and not has_directive:
                    cat = "UNRESOLVED_QUESTION"
                    prefix = f"Unresolved Question ({turn.speaker}):"
                    rationale = (
                        f"Architectural inquiry raised by Fodoh in session '{session.title}' (Turn {turn.turn_index}). "
                        "Preserved as an open question guiding system development; pending human memory review."
                    )
                else:
                    cat = "HUMAN_DIRECTIVE"
                    prefix = f"Human Directive ({turn.speaker}):"
                    rationale = (
                        f"Direct authoritative guidance from Fodoh in session '{session.title}' (Turn {turn.turn_index}). "
                        "Human-authored constraint; requires explicit human authorization for permanent memory retention."
                    )

            else:
                # Agent outputs inherit synthetic flag from session mode
                is_synthetic = not session.is_live
                cat = "UNVERIFIED_PROPOSAL"

                if turn.speaker == "Antigravity":
                    prefix = "Technical Critique (Antigravity):" if has_disagreement else "Technical Proposal (Antigravity):"
                    rationale = (
                        f"Technical implementation proposal by Antigravity in session '{session.title}' (Turn {turn.turn_index}). "
                        "Agent consensus is never treated as confirmed fact. Requires explicit human authorization."
                    )
                elif turn.speaker == "OpenAI-Agent":
                    prefix = "Analytical Challenge (OpenAI-Agent):" if has_disagreement else "Analytical Proposal (OpenAI-Agent):"
                    rationale = (
                        f"Analytical assessment by OpenAI-Agent in session '{session.title}' (Turn {turn.turn_index}). "
                        "Agent consensus is never treated as confirmed fact. Requires explicit human authorization."
                    )
                elif turn.speaker == "Genesis":
                    prefix = "Orchestrator Reflection (Genesis):"
                    rationale = (
                        f"Voice orchestrator reflection by Genesis in session '{session.title}' (Turn {turn.turn_index}). "
                        "Agent consensus is never treated as confirmed fact. Requires explicit human authorization."
                    )
                else:
                    prefix = f"Participant Statement ({turn.speaker}):"
                    rationale = f"Statement from Turn {turn.turn_index}. Requires human authorization."

                if has_disagreement:
                    rationale += " [Analytical counter-perspective/disagreement explicitly preserved.]"

            # 4. Content Formatting: Preserve full authentic content for substantive turns
            candidate_text = f"{prefix} {content}"

            cand = {
                "content": candidate_text,
                "speaker": turn.speaker,
                "category": cat,
                "session_id": session.session_id,
                "turn_id": turn.turn_index,
                "transcript_path": str(Path(session.session_id).name),
                "authorization_record": f"comm_transit_{session.session_id}_turn_{turn.turn_index}:TRANSIT_ONLY",
                "authorization_scope": "COMMUNICATION_TRANSIT",
                "is_synthetic": is_synthetic,
                "rationale": rationale,
                "extraction_method": "deterministic_offline",
            }
            candidates.append(cand)

        return candidates
