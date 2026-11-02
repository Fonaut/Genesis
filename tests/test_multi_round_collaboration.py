"""
Unit and Regression Tests for Phase 2: Flexible Multi-Round Collaboration (Milestone A)
Verifies:
- Session creation and atomic disk persistence
- Sequential turn ordering and idempotency token deduplication
- Human-only messages with $0 cost
- Crash recovery: in-flight turns transitioning to UNKNOWN_OUTCOME without auto-replay
- Crash recovery: stale gate invalidation on restart
- Session pause, resume, and termination state lifecycle
- Bounded turn cap enforcement
"""

import json
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from hub.phase0 import HubSafetyError
from interface.engine import CommunicationInterfaceEngine
from interface.group_session import MultiRoundSessionManager
from interface.models import GroupSessionState, GroupTurnRecord


class TestMultiRoundCollaborationMilestoneA(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_multi_round_"))
        self.engine = CommunicationInterfaceEngine(log_dir=self.test_dir)
        self.manager = MultiRoundSessionManager(log_dir=self.test_dir, engine=self.engine)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_create_and_atomic_file_persistence(self):
        """Verify session creation writes an atomic JSON file and registers Fodoh's opening prompt as Turn 0 ($0 cost)."""
        session = self.manager.create_session(
            title="Architectural Review",
            is_live=False,
            max_turns=6,
            initial_prompt="How should our three agents coordinate working memory?",
        )

        self.assertTrue(session.session_id.startswith("group_"))
        self.assertEqual(session.title, "Architectural Review")
        self.assertEqual(session.status, "WAITING_FOR_HUMAN")
        self.assertEqual(session.max_turns, 6)
        self.assertEqual(len(session.turns), 1)

        t0 = session.turns[0]
        self.assertEqual(t0.turn_index, 0)
        self.assertEqual(t0.speaker, "Fodoh")
        self.assertEqual(t0.speaker_type, "HUMAN")
        self.assertEqual(t0.content, "How should our three agents coordinate working memory?")
        self.assertEqual(t0.cost_usd_est, 0.0)
        self.assertEqual(t0.latency_sec, 0.0)
        self.assertEqual(t0.model, "human")

        # Verify persisted on disk
        saved = self.manager.get_session(session.session_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.session_id, session.session_id)
        self.assertEqual(len(saved.turns), 1)

    def test_append_turn_sequential_and_idempotent(self):
        """Verify turns append sequentially and duplicate idempotency tokens are strictly rejected."""
        session = self.manager.create_session(title="Turn Test", max_turns=5)
        self.assertEqual(len(session.turns), 0)

        turn_token = f"tok_{uuid.uuid4().hex}"
        turn1 = GroupTurnRecord(
            turn_id="turn_1_abc",
            turn_index=0,
            speaker="Antigravity",
            speaker_type="AGENT",
            content="Proposal: Use a working memory buffer.",
            timestamp="2026-10-22T14:15:00Z",
            model="mock",
            cost_usd_est=0.0,
            latency_sec=0.1,
            idempotency_token=turn_token,
            status="COMMITTED",
        )

        updated = self.manager.append_turn(session.session_id, turn1)
        self.assertEqual(len(updated.turns), 1)
        self.assertEqual(updated.turns[0].speaker, "Antigravity")

        # Duplicate token rejection test
        with self.assertRaises(ValueError) as ctx:
            self.manager.append_turn(session.session_id, turn1)
        self.assertIn("Duplicate turn rejected", str(ctx.exception))

        # Out-of-order sequence index rejection test
        turn_bad_index = GroupTurnRecord(
            turn_id="turn_5_xyz",
            turn_index=5,  # Expected index is 1
            speaker="OpenAI-Agent",
            speaker_type="AGENT",
            content="Skipped turn index.",
            timestamp="2026-10-22T14:15:05Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        with self.assertRaises(ValueError) as ctx:
            self.manager.append_turn(session.session_id, turn_bad_index)
        self.assertIn("Turn index mismatch", str(ctx.exception))

    def test_crash_recovery_in_flight_turn_to_unknown_outcome(self):
        """Verify crash during EXECUTING_TURN marks session as UNKNOWN_OUTCOME without auto-replay."""
        session = self.manager.create_session(title="Crash Recovery Test", initial_prompt="Initial query")

        # Simulate turn 1 being dispatched and server crashing mid-execution
        res_id = self.engine.reserve_api_budget(0.000850, source="test_crash")
        session.status = "EXECUTING_TURN"
        session.in_flight_reservation_id = res_id
        self.manager._save_session_atomic(session)

        # Confirm reservation is active in engine
        self.assertEqual(self.engine.get_active_reservations_total(), 0.000850)

        # Simulate server reboot and crash recovery execution
        recovered_count = self.manager.recover_sessions_on_startup()
        self.assertEqual(recovered_count, 1)

        # Verify session state post-recovery
        recovered_sess = self.manager.get_session(session.session_id)
        self.assertEqual(recovered_sess.status, "UNKNOWN_OUTCOME")
        self.assertIn("Turn outcome is uncertain", recovered_sess.error_message)
        self.assertIn("Previous turns are preserved", recovered_sess.error_message)
        # Prior turn 0 preserved
        self.assertEqual(len(recovered_sess.turns), 1)
        self.assertEqual(recovered_sess.turns[0].speaker, "Fodoh")

        # Verify orphaned budget reservation was safely released
        self.assertEqual(self.engine.get_active_reservations_total(), 0.0)

    def test_crash_recovery_stale_gate_invalidation(self):
        """Verify crash during DISPATCH_PENDING invalidates stale approval gate on restart."""
        session = self.manager.create_session(title="Stale Gate Test")
        session.status = "DISPATCH_PENDING"
        session.active_gate = {
            "gate_nonce": "nonce_12345",
            "target_agent": "Antigravity",
            "proposed_payload": "Run system diagnostics.",
        }
        self.manager._save_session_atomic(session)

        # Restart and recover
        recovered_count = self.manager.recover_sessions_on_startup()
        self.assertEqual(recovered_count, 1)

        recovered_sess = self.manager.get_session(session.session_id)
        self.assertEqual(recovered_sess.status, "WAITING_FOR_HUMAN")
        self.assertIsNone(recovered_sess.active_gate)
        self.assertIn("gate was invalidated due to server restart", recovered_sess.error_message)

    def test_session_pause_resume_terminate_lifecycle(self):
        """Verify clean transitions across PAUSED, WAITING_FOR_HUMAN, and COMPLETED states."""
        session = self.manager.create_session(title="Lifecycle Test")
        self.assertEqual(session.status, "WAITING_FOR_HUMAN")

        # Pause
        paused = self.manager.pause_session(session.session_id)
        self.assertEqual(paused.status, "PAUSED")

        # Resume
        resumed = self.manager.resume_session(session.session_id)
        self.assertEqual(resumed.status, "WAITING_FOR_HUMAN")

        # Terminate
        terminated = self.manager.terminate_session(session.session_id, reason="Topic concluded by Fodoh")
        self.assertEqual(terminated.status, "COMPLETED")
        self.assertEqual(terminated.error_message, "Topic concluded by Fodoh")

        # Terminal state cannot be paused or resumed
        with self.assertRaises(HubSafetyError):
            self.manager.pause_session(session.session_id)

        with self.assertRaises(HubSafetyError):
            self.manager.resume_session(session.session_id)

    def test_bounded_turns_limit_completion(self):
        """Verify session automatically marks COMPLETED when bounded turn limit is reached."""
        session = self.manager.create_session(title="Bounded Cap Test", max_turns=2)
        self.assertEqual(session.max_turns, 2)

        turn0 = GroupTurnRecord(
            turn_id="t0",
            turn_index=0,
            speaker="Fodoh",
            speaker_type="HUMAN",
            content="First human prompt.",
            timestamp="2026-10-22T14:20:00Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        self.manager.append_turn(session.session_id, turn0)

        turn1 = GroupTurnRecord(
            turn_id="t1",
            turn_index=1,
            speaker="Antigravity",
            speaker_type="AGENT",
            content="Final turn response.",
            timestamp="2026-10-22T14:20:05Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        s_final = self.manager.append_turn(session.session_id, turn1)

        self.assertEqual(len(s_final.turns), 2)
        self.assertEqual(s_final.status, "COMPLETED")
        self.assertIn("reached bounded turn limit", s_final.error_message)

    def test_interject_human_message_zero_cost(self):
        """Verify human interjection appends a turn at $0 cost and zero model execution."""
        session = self.manager.create_session(title="Human Interject Test", initial_prompt="Initial prompt")
        self.assertEqual(len(session.turns), 1)

        updated = self.manager.interject_human_message(
            session_id=session.session_id,
            message="Wait, let us clarify the security boundaries first.",
            recommended_next_speaker="OpenAI-Agent",
        )

        self.assertEqual(len(updated.turns), 2)
        t1 = updated.turns[1]
        self.assertEqual(t1.speaker, "Fodoh")
        self.assertEqual(t1.speaker_type, "HUMAN")
        self.assertEqual(t1.content, "Wait, let us clarify the security boundaries first.")
        self.assertEqual(t1.cost_usd_est, 0.0)
        self.assertEqual(t1.latency_sec, 0.0)
        self.assertEqual(t1.model, "human")
        self.assertEqual(updated.recommended_next_speaker, "OpenAI-Agent")
        self.assertEqual(updated.status, "WAITING_FOR_HUMAN")

        # Empty message rejection
        with self.assertRaises(ValueError):
            self.manager.interject_human_message(session.session_id, "   ")

    def test_prepare_and_approve_agent_turn(self):
        """Verify staging an agent turn, creating an approval gate nonce, and dispatching upon approval."""
        session = self.manager.create_session(title="Staged Dispatch Test", initial_prompt="Probe prompt")
        
        # Prepare agent turn for Antigravity
        sess_staged, gate = self.manager.prepare_agent_turn(
            session_id=session.session_id,
            target_agent="Antigravity",
            prompt_override="Provide technical execution analysis.",
        )

        self.assertEqual(sess_staged.status, "DISPATCH_PENDING")
        self.assertIsNotNone(sess_staged.active_gate)
        self.assertEqual(gate["target_agent"], "Antigravity")
        self.assertTrue(gate["gate_nonce"].startswith("gate_"))
        self.assertIn("Provide technical execution analysis.", gate["proposed_payload"])

        # Approve and dispatch (offline mode)
        sess_done = self.manager.dispatch_agent_turn(
            session_id=session.session_id,
            gate_nonce=gate["gate_nonce"],
            action="approve",
        )

        self.assertEqual(sess_done.status, "WAITING_FOR_HUMAN")
        self.assertIsNone(sess_done.active_gate)
        self.assertEqual(len(sess_done.turns), 2)

        t_agent = sess_done.turns[1]
        self.assertEqual(t_agent.speaker, "Antigravity")
        self.assertEqual(t_agent.speaker_type, "AGENT")
        self.assertIn("Antigravity technical assessment", t_agent.content)
        self.assertEqual(t_agent.status, "COMMITTED")

    def test_reject_agent_turn_gate(self):
        """Verify human rejection clears the gate and returns session to WAITING_FOR_HUMAN without dispatch."""
        session = self.manager.create_session(title="Reject Turn Test", initial_prompt="Opening question")
        sess_staged, gate = self.manager.prepare_agent_turn(
            session_id=session.session_id,
            target_agent="OpenAI-Agent",
        )
        self.assertEqual(sess_staged.status, "DISPATCH_PENDING")

        sess_rejected = self.manager.dispatch_agent_turn(
            session_id=session.session_id,
            gate_nonce=gate["gate_nonce"],
            action="reject",
        )

        self.assertEqual(sess_rejected.status, "WAITING_FOR_HUMAN")
        self.assertIsNone(sess_rejected.active_gate)
        self.assertIn("cancelled by Fodoh", sess_rejected.error_message)
        # Turn count remains 1 (only human prompt)
        self.assertEqual(len(sess_rejected.turns), 1)

    def test_invalid_gate_nonce_rejected(self):
        """Verify dispatching with wrong or expired nonce is strictly blocked."""
        session = self.manager.create_session(title="Nonce Test", initial_prompt="Prompt")
        self.manager.prepare_agent_turn(session_id=session.session_id, target_agent="Genesis")

        with self.assertRaises(HubSafetyError) as ctx:
            self.manager.dispatch_agent_turn(
                session_id=session.session_id,
                gate_nonce="bad_nonce_fake_123",
                action="approve",
            )
        self.assertIn("Invalid or expired gate nonce", str(ctx.exception))

    def test_kill_switch_blocks_dispatch(self):
        """Verify emergency kill switch immediately blocks agent turn dispatch."""
        session = self.manager.create_session(title="Kill Switch Dispatch Test")
        sess_staged, gate = self.manager.prepare_agent_turn(
            session_id=session.session_id,
            target_agent="Antigravity",
        )

        # Trigger kill switch
        self.engine.trigger_kill_switch()
        try:
            with self.assertRaises(HubSafetyError) as ctx:
                self.manager.dispatch_agent_turn(
                    session_id=session.session_id,
                    gate_nonce=gate["gate_nonce"],
                    action="approve",
                )
            self.assertIn("Emergency kill switch is active", str(ctx.exception))
        finally:
            self.engine.clear_kill_switch()

    def test_recover_turn_action_from_unknown_or_failed(self):
        """Verify recovering a session marked as UNKNOWN_OUTCOME or FAILED."""
        session = self.manager.create_session(title="Recovery Action Test")
        session.status = "UNKNOWN_OUTCOME"
        session.error_message = "Turn outcome uncertain."
        self.manager._save_session_atomic(session)

        recovered = self.manager.recover_turn_action(session.session_id, action="retry")
        self.assertEqual(recovered.status, "WAITING_FOR_HUMAN")
        self.assertIn("Session recovered by Fodoh", recovered.error_message)

    def test_context_history_formatting(self):
        """Verify format_session_history_context accurately reflects speakers and labels."""
        session = self.manager.create_session(title="History Format Test", initial_prompt="Prompt 1")
        session = self.manager.interject_human_message(session.session_id, "Fodoh follow up")
        
        ctx_str = self.manager.format_session_history_context(session)
        self.assertIn("[Fodoh]:\nPrompt 1", ctx_str)
        self.assertIn("[Fodoh]:\nFodoh follow up", ctx_str)

    def test_group_memory_extractor_deterministic(self):
        """Verify GroupMemoryExtractor deterministically creates structured proposals without API calls."""
        from interface.group_session import GroupMemoryExtractor
        session = self.manager.create_session(title="Memory Extraction Test", initial_prompt="How do we sandbox external execution?")
        
        # Add Antigravity turn
        turn_ag = GroupTurnRecord(
            turn_id="t1",
            turn_index=1,
            speaker="Antigravity",
            speaker_type="AGENT",
            content="We use macOS Seatbelt (/usr/bin/sandbox-exec) with a strict Scheme profile to enforce filesystem isolation.",
            timestamp="2026-10-22T14:30:00Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        self.manager.append_turn(session.session_id, turn_ag)
        session = self.manager.get_session(session.session_id)

        candidates = GroupMemoryExtractor.extract_candidates(session)
        self.assertEqual(len(candidates), 2)

        # Candidate 0 (Fodoh question -> UNRESOLVED_QUESTION, non-synthetic)
        c0 = candidates[0]
        self.assertEqual(c0["speaker"], "Fodoh")
        self.assertEqual(c0["category"], "UNRESOLVED_QUESTION")
        self.assertEqual(c0["authorization_scope"], "COMMUNICATION_TRANSIT")
        self.assertEqual(c0["extraction_method"], "deterministic_offline")
        self.assertFalse(c0["is_synthetic"])

        # Candidate 1 (Antigravity proposal -> UNVERIFIED_PROPOSAL, synthetic)
        c1 = candidates[1]
        self.assertEqual(c1["speaker"], "Antigravity")
        self.assertEqual(c1["category"], "UNVERIFIED_PROPOSAL")
        self.assertIn("Technical Proposal (Antigravity):", c1["content"])
        self.assertIn("macOS Seatbelt", c1["content"])
        self.assertEqual(c1["authorization_scope"], "COMMUNICATION_TRANSIT")
        self.assertEqual(c1["extraction_method"], "deterministic_offline")
        self.assertTrue(c1["is_synthetic"])

        # Add OpenAI-Agent turn with critique/disagreement
        turn_oa = GroupTurnRecord(
            turn_id="t2",
            turn_index=2,
            speaker="OpenAI-Agent",
            speaker_type="AGENT",
            content="However, I critique this approach because sandbox-exec is deprecated on newer macOS versions. We should consider containerization alternatives.",
            timestamp="2026-10-22T14:31:00Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        self.manager.append_turn(session.session_id, turn_oa)

        # Add explicit human directive
        turn_dir = GroupTurnRecord(
            turn_id="t3",
            turn_index=3,
            speaker="Fodoh",
            speaker_type="HUMAN",
            content="Directive: Ensure all subprocess execution enforces strict scheme profiles.",
            timestamp="2026-10-22T14:32:00Z",
            idempotency_token=f"tok_{uuid.uuid4().hex}",
        )
        self.manager.append_turn(session.session_id, turn_dir)
        session = self.manager.get_session(session.session_id)

        candidates = GroupMemoryExtractor.extract_candidates(session)
        self.assertEqual(len(candidates), 4)

        c2 = candidates[2]
        self.assertEqual(c2["speaker"], "OpenAI-Agent")
        self.assertEqual(c2["category"], "UNVERIFIED_PROPOSAL")
        self.assertIn("Analytical Challenge (OpenAI-Agent):", c2["content"])
        self.assertIn("Analytical counter-perspective/disagreement explicitly preserved", c2["rationale"])
        self.assertTrue(c2["is_synthetic"])

        c3 = candidates[3]
        self.assertEqual(c3["speaker"], "Fodoh")
        self.assertEqual(c3["category"], "HUMAN_DIRECTIVE")
        self.assertIn("Ensure all subprocess execution enforces strict scheme profiles.", c3["content"])
        self.assertFalse(c3["is_synthetic"])

    def test_extract_memories_stages_and_deduplicates(self):
        """Verify session manager extracts candidates, stages them into review queue, and deduplicates on repeat calls."""
        session = self.manager.create_session(
            title="Deduplication Test",
            initial_prompt="How should our multi-agent architecture coordinate shared working memory?",
        )
        
        # First extraction: stages 1 candidate
        res1 = self.manager.extract_memories(session.session_id, stage_in_review_queue=True)
        self.assertEqual(res1["extracted_count"], 1)
        self.assertEqual(res1["staged_count"], 1)
        self.assertEqual(res1["skipped_duplicates_count"], 0)

        # Second extraction: recognizes existing (session_id, turn_id) and skips duplicate staging
        res2 = self.manager.extract_memories(session.session_id, stage_in_review_queue=True)
        self.assertEqual(res2["extracted_count"], 1)
        self.assertEqual(res2["staged_count"], 0)
        self.assertEqual(res2["skipped_duplicates_count"], 1)


    def test_path_traversal_session_id_rejected(self):
        """Verify path traversal in session_id raises HubSafetyError."""
        with self.assertRaises(HubSafetyError) as ctx:
            self.manager.get_session("../../etc/passwd")
        self.assertIn("Path traversal rejected", str(ctx.exception))

        with self.assertRaises(HubSafetyError) as ctx:
            self.manager.get_session("sub/folder")
        self.assertIn("Path traversal rejected", str(ctx.exception))


class TestMultiRoundCollaborationEndpoints(unittest.TestCase):
    """Integration tests for Phase 2 REST API endpoints."""

    def setUp(self):
        from fastapi.testclient import TestClient
        from interface.server import app, engine
        self.client = TestClient(app)
        self.engine = engine

        self.test_dir = Path(tempfile.mkdtemp(prefix="test_group_endpoints_"))
        self.orig_log_dir = self.engine.log_dir
        self.orig_convs_file = self.engine.conversations_file
        self.orig_ledger_file = self.engine.cost_ledger_file
        self.orig_sessions_dir = self.engine.group_session_manager.sessions_dir
        self.orig_staging_file = self.engine.collaborative_manager.staging_file
        self.orig_active_sid = self.engine.group_session_manager.active_session_id

        self.engine.log_dir = self.test_dir
        self.engine.conversations_file = self.test_dir / "conversations.json"
        self.engine.cost_ledger_file = self.test_dir / "cost_ledger.json"
        self.engine.group_session_manager.log_dir = self.test_dir
        self.engine.group_session_manager.sessions_dir = self.test_dir / "group_sessions"
        self.engine.group_session_manager.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.engine.collaborative_manager.staging_file = self.test_dir / "collaborative_staging.json"
        self.engine.group_session_manager.active_session_id = None

    def tearDown(self):
        self.engine.log_dir = self.orig_log_dir
        self.engine.conversations_file = self.orig_convs_file
        self.engine.cost_ledger_file = self.orig_ledger_file
        self.engine.group_session_manager.log_dir = self.orig_log_dir
        self.engine.group_session_manager.sessions_dir = self.orig_sessions_dir
        self.engine.collaborative_manager.staging_file = self.orig_staging_file
        self.engine.group_session_manager.active_session_id = self.orig_active_sid
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_csrf_and_origin_protection(self):
        """Verify cross-origin or untrusted requests to state-changing endpoints are blocked with 403."""
        # 1. Cross-site Sec-Fetch-Site rejected
        res = self.client.post(
            "/api/sessions/create",
            json={"title": "CSRF Attack"},
            headers={"sec-fetch-site": "cross-site"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("CSRF protection", res.json()["detail"])

        # 2. Unauthorized Origin header rejected
        res2 = self.client.post(
            "/api/sessions/create",
            json={"title": "Malicious Origin"},
            headers={"origin": "http://evil-attacker.com"},
        )
        self.assertEqual(res2.status_code, 403)
        self.assertIn("not authorized", res2.json()["detail"])

        # 3. Authorized Origin accepted
        res3 = self.client.post(
            "/api/sessions/create",
            json={"title": "Authorized Origin Session"},
            headers={"origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(res3.status_code, 200)

    def test_full_group_session_api_flow(self):
        """Test complete multi-round lifecycle over REST API: create -> interject -> prepare -> dispatch -> pause -> resume -> terminate."""
        # 1. Create session
        res = self.client.post("/api/sessions/create", json={
            "title": "API Triad Review",
            "is_live": False,
            "max_turns": 8,
            "initial_prompt": "What are our architectural principles?",
        })
        self.assertEqual(res.status_code, 200)
        sess = res.json()
        sid = sess["session_id"]
        self.assertEqual(sess["status"], "WAITING_FOR_HUMAN")
        self.assertEqual(len(sess["turns"]), 1)
        self.assertEqual(sess["turns"][0]["speaker"], "Fodoh")

        # 2. Get active session
        res_act = self.client.get("/api/sessions/active")
        self.assertEqual(res_act.status_code, 200)
        self.assertEqual(res_act.json()["session"]["session_id"], sid)

        # 3. Interject human message ($0 cost)
        res_interject = self.client.post("/api/sessions/human/interject", json={
            "session_id": sid,
            "message": "Let us have Antigravity summarize first.",
            "recommended_next_speaker": "Antigravity",
        })
        self.assertEqual(res_interject.status_code, 200)
        sess_inter = res_interject.json()
        self.assertEqual(len(sess_inter["turns"]), 2)
        self.assertEqual(sess_inter["turns"][1]["speaker"], "Fodoh")
        self.assertEqual(sess_inter["cumulative_cost_usd"], 0.0)

        # 4. Prepare agent turn (staging approval gate)
        res_prep = self.client.post("/api/sessions/turn/prepare", json={
            "session_id": sid,
            "target_agent": "Antigravity",
            "prompt_override": "Summarize the execution boundary.",
        })
        self.assertEqual(res_prep.status_code, 200)
        prep_data = res_prep.json()
        gate = prep_data["gate"]
        nonce = gate["gate_nonce"]
        self.assertTrue(nonce.startswith("gate_"))
        self.assertEqual(prep_data["session"]["status"], "DISPATCH_PENDING")

        # 5. Dispatch agent turn (approve)
        res_disp = self.client.post("/api/sessions/turn/dispatch", json={
            "session_id": sid,
            "gate_nonce": nonce,
            "action": "approve",
        })
        self.assertEqual(res_disp.status_code, 200)
        sess_disp = res_disp.json()
        self.assertEqual(sess_disp["status"], "WAITING_FOR_HUMAN")
        self.assertEqual(len(sess_disp["turns"]), 3)
        self.assertEqual(sess_disp["turns"][2]["speaker"], "Antigravity")
        self.assertEqual(sess_disp["turns"][2]["speaker_type"], "AGENT")

        # 6. Pause session
        res_pause = self.client.post("/api/sessions/action", json={
            "session_id": sid,
            "action": "pause",
        })
        self.assertEqual(res_pause.status_code, 200)
        self.assertEqual(res_pause.json()["status"], "PAUSED")

        # 7. Resume session
        res_resume = self.client.post("/api/sessions/action", json={
            "session_id": sid,
            "action": "resume",
        })
        self.assertEqual(res_resume.status_code, 200)
        self.assertEqual(res_resume.json()["status"], "WAITING_FOR_HUMAN")

        # 8. Terminate session
        res_term = self.client.post("/api/sessions/action", json={
            "session_id": sid,
            "action": "terminate",
        })
        self.assertEqual(res_term.status_code, 200)
        self.assertEqual(res_term.json()["status"], "COMPLETED")

        # 9. Extract candidate memories via REST API
        res_extract = self.client.post("/api/sessions/extract-memories", json={
            "session_id": sid,
            "extraction_method": "deterministic_offline",
            "stage_in_review_queue": True,
        })
        self.assertEqual(res_extract.status_code, 200)
        ext_data = res_extract.json()
        self.assertEqual(ext_data["extraction_method"], "deterministic_offline")
        self.assertEqual(ext_data["extracted_count"], 2)  # 2 substantive human turns; routine mock ping filtered out
        self.assertEqual(ext_data["staged_count"], 2)

        # 10. List sessions
        res_list = self.client.get("/api/sessions")
        self.assertEqual(res_list.status_code, 200)
        session_ids = [s["session_id"] for s in res_list.json()]
        self.assertIn(sid, session_ids)

        # 11. Human review of staged group candidate memory
        res_staging = self.client.get("/api/memories/staging?status=PENDING_REVIEW")
        self.assertEqual(res_staging.status_code, 200)
        pending_items = res_staging.json()
        group_items = [it for it in pending_items if it.get("session_id") == sid]
        self.assertTrue(len(group_items) >= 1)

        target_mem = group_items[0]
        mem_id = target_mem["memory_id"]

        # Approve memory as Fodoh
        res_review = self.client.post("/api/memories/staging/review", json={
            "memory_id": mem_id,
            "action": "approve",
            "reviewed_by": "Fodoh",
        })
        self.assertEqual(res_review.status_code, 200)
        rev_data = res_review.json()
        self.assertEqual(rev_data["status"], "SUCCESS")
        self.assertEqual(rev_data["memory"]["status"], "APPROVED")
        self.assertEqual(rev_data["memory"]["authorization_scope"], "MEMORY_APPROVAL")
        self.assertEqual(rev_data["memory"]["reviewed_by"], "Fodoh")

    def test_group_dispatch_genesis_and_openai_offline(self):
        """Verify staging and dispatching Genesis and OpenAI-Agent in an offline group session."""
        res_create = self.client.post("/api/sessions/create", json={
            "title": "Agent Turn Multi-Participant Test",
            "is_live": False,
            "initial_prompt": "Triad coordination question.",
        })
        sid = res_create.json()["session_id"]

        # 1. Dispatch Genesis
        res_prep_gen = self.client.post("/api/sessions/turn/prepare", json={
            "session_id": sid,
            "target_agent": "Genesis",
        })
        self.assertEqual(res_prep_gen.status_code, 200)
        nonce_gen = res_prep_gen.json()["gate"]["gate_nonce"]

        res_disp_gen = self.client.post("/api/sessions/turn/dispatch", json={
            "session_id": sid,
            "gate_nonce": nonce_gen,
            "action": "approve",
        })
        self.assertEqual(res_disp_gen.status_code, 200)
        sess_gen = res_disp_gen.json()
        self.assertEqual(sess_gen["turns"][-1]["speaker"], "Genesis")
        self.assertEqual(sess_gen["turns"][-1]["speaker_type"], "AGENT")

        # 2. Dispatch OpenAI-Agent
        res_prep_oa = self.client.post("/api/sessions/turn/prepare", json={
            "session_id": sid,
            "target_agent": "OpenAI-Agent",
        })
        self.assertEqual(res_prep_oa.status_code, 200)
        nonce_oa = res_prep_oa.json()["gate"]["gate_nonce"]

        res_disp_oa = self.client.post("/api/sessions/turn/dispatch", json={
            "session_id": sid,
            "gate_nonce": nonce_oa,
            "action": "approve",
        })
        self.assertEqual(res_disp_oa.status_code, 200)
        sess_oa = res_disp_oa.json()
        self.assertEqual(sess_oa["turns"][-1]["speaker"], "OpenAI-Agent")
        self.assertEqual(sess_oa["turns"][-1]["speaker_type"], "AGENT")

    def test_recover_turn_endpoint_action(self):
        """Verify /api/sessions/action with 'recover_turn' recovers UNKNOWN_OUTCOME session."""
        res_create = self.client.post("/api/sessions/create", json={
            "title": "Recovery API Test",
            "initial_prompt": "Query before crash",
        })
        sid = res_create.json()["session_id"]

        # Artificially set UNKNOWN_OUTCOME on disk
        sess = self.engine.group_session_manager.get_session(sid)
        sess.status = "UNKNOWN_OUTCOME"
        sess.error_message = "Turn interrupted during restart."
        self.engine.group_session_manager._save_session_atomic(sess)

        # Recover via retry action
        res_rec = self.client.post("/api/sessions/action", json={
            "session_id": sid,
            "action": "recover_turn",
            "recovery_action": "retry",
        })
        self.assertEqual(res_rec.status_code, 200)
        recovered = res_rec.json()
        self.assertEqual(recovered["status"], "WAITING_FOR_HUMAN")
        self.assertIn("Session recovered by Fodoh", recovered["error_message"])



if __name__ == "__main__":
    unittest.main()
