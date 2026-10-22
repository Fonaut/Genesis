"""
Tests for Genesis Communication Interface
Verifies:
- Root HTML serving
- System status endpoint
- Individual agent routing (Genesis, Antigravity, OpenAI-Agent)
- Supervised Triad initiation and 3-step UI approval gate flow
- Triad rejection and safe cancellation
- Emergency kill-switch trigger and recovery
- Development Journal integration endpoints
- Memory isolation invariant (zero writes to memories.json or ChromaDB)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from interface.server import app, engine
from hub.phase0 import PROJECT_ROOT


class TestCommunicationInterface(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.test_log_dir = Path(tempfile.mkdtemp(prefix="test_interface_logs_"))
        # Point engine log dir to temporary folder
        engine.log_dir = self.test_log_dir
        engine.conversations_file = self.test_log_dir / "conversations.json"
        engine.cost_ledger_file = self.test_log_dir / "cost_ledger.json"
        engine.active_triad_session = None

        # Isolate cost governor
        self.original_cumulative_cost = engine.governor.cumulative_cost_usd
        engine.governor.cumulative_cost_usd = 0.0
        engine._active_reservations = {}

        # Isolate collaborative staging store to test directory
        self.original_staging_file = engine.collaborative_manager.staging_file
        engine.collaborative_manager.staging_file = self.test_log_dir / "collaborative_staging.json"

        # Ensure kill-switch file is absent
        stop_file = PROJECT_ROOT / ".hub_stopped"
        if stop_file.exists():
            stop_file.unlink()

    def tearDown(self):
        engine._active_reservations = {}
        engine.governor.cumulative_cost_usd = self.original_cumulative_cost
        engine.collaborative_manager.staging_file = self.original_staging_file
        stop_file = PROJECT_ROOT / ".hub_stopped"
        if stop_file.exists():
            stop_file.unlink()
        shutil.rmtree(self.test_log_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Server Root & System Status
    # -------------------------------------------------------------------------

    def test_server_root_serves_html(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Genesis Communication Interface", res.text)
        self.assertIn("EMERGENCY STOP", res.text)
        self.assertIn("costCapVal", res.text)
        self.assertIn("sandboxProfileVal", res.text)

    def test_status_endpoint(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("kill_switch_active", data)
        self.assertFalse(data["kill_switch_active"])
        self.assertIn("participants", data)
        self.assertEqual(len(data["participants"]), 3)
        self.assertEqual(data["max_cost_usd"], 0.05)
        self.assertEqual(data["cumulative_cost_usd"], 0.0)
        self.assertIn(data["sandbox_status"], ["Seatbelt Active", "Filesystem Isolated"])
        self.assertIn("sandbox_path", data)
        self.assertFalse(data["is_live_active"])
        self.assertIsNone(data["active_triad_session"])

    # -------------------------------------------------------------------------
    # 2. Individual Agent Routing (1-on-1 Mode)
    # -------------------------------------------------------------------------

    def test_individual_chat_genesis_offline(self):
        payload = {
            "recipient": "Genesis",
            "message": "Hello Genesis, can you confirm your companion architecture?",
            "is_live": False,
        }
        res = self.client.post("/api/chat/individual", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["speaker"], "Genesis")
        self.assertEqual(data["recipient"], "Fodoh")
        self.assertIn("Hello Fodoh", data["content"])
        self.assertIn("offline dry-run mode", data["content"])
        self.assertEqual(data["cost_usd_est"], 0.0)

    def test_individual_chat_antigravity_offline(self):
        payload = {
            "recipient": "Antigravity",
            "message": "Check filesystem verification",
            "is_live": False,
        }
        res = self.client.post("/api/chat/individual", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["speaker"], "Antigravity")
        self.assertIn("Antigravity technical assessment for Fodoh", data["content"])

    def test_individual_chat_openai_agent_offline(self):
        payload = {
            "recipient": "OpenAI-Agent",
            "message": "Critique our multi-agent protocol",
            "is_live": False,
        }
        res = self.client.post("/api/chat/individual", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["speaker"], "OpenAI-Agent")
        self.assertTrue(len(data["content"]) > 0)

    def test_individual_chat_invalid_recipient(self):
        payload = {
            "recipient": "UnknownRobot",
            "message": "Hello",
            "is_live": False,
        }
        res = self.client.post("/api/chat/individual", json=payload)
        self.assertEqual(res.status_code, 422)

    def test_individual_chat_empty_message_rejected(self):
        payload = {
            "recipient": "Genesis",
            "message": "   ",
            "is_live": False,
        }
        res = self.client.post("/api/chat/individual", json=payload)
        self.assertEqual(res.status_code, 422)

    # -------------------------------------------------------------------------
    # 3. Supervised Triad Discussion with UI Approval Gates
    # -------------------------------------------------------------------------

    def test_triad_initiation_and_three_gate_approval_flow_mock(self):
        """
        Tests complete 3-agent supervised sequence:
        1. Initiate -> Gate 1 pending (Probe -> Antigravity)
        2. Approve Gate 1 -> Turn 1 executes -> Gate 2 pending (Antigravity -> OpenAI-Agent)
        3. Approve Gate 2 -> Turn 2 executes -> Gate 3 pending (Context -> Genesis)
        4. Approve Gate 3 -> Turn 3 executes -> COMPLETED
        """
        # Step 1: Initiate Triad
        init_res = self.client.post("/api/chat/triad/initiate", json={
            "probe_prompt": "Test division of labor among Intent, Execution, and Critique.",
            "is_live": False,
        })
        self.assertEqual(init_res.status_code, 200)
        init_data = init_res.json()
        self.assertEqual(init_data["status"], "PENDING_APPROVAL")
        self.assertIsNotNone(init_data["pending_gate"])

        gate_1 = init_data["pending_gate"]
        self.assertIn("gate_1", gate_1["approval_id"])
        self.assertEqual(gate_1["recipient"], "Antigravity")

        # Step 2: Approve Gate 1
        resp_1 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_1["approval_id"],
            "action": "approve",
        })
        self.assertEqual(resp_1.status_code, 200)
        state_1 = resp_1.json()
        self.assertEqual(len(state_1["turns"]), 1)
        self.assertEqual(state_1["turns"][0]["speaker"], "Antigravity")
        self.assertEqual(state_1["status"], "PENDING_APPROVAL")

        gate_2 = state_1["pending_gate"]
        self.assertIn("gate_2", gate_2["approval_id"])
        self.assertEqual(gate_2["recipient"], "OpenAI-Agent")

        # Step 3: Approve Gate 2
        resp_2 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_2["approval_id"],
            "action": "approve",
        })
        self.assertEqual(resp_2.status_code, 200)
        state_2 = resp_2.json()
        self.assertEqual(len(state_2["turns"]), 2)
        self.assertEqual(state_2["turns"][1]["speaker"], "OpenAI-Agent")

        gate_3 = state_2["pending_gate"]
        self.assertIn("gate_3", gate_3["approval_id"])
        self.assertEqual(gate_3["recipient"], "Genesis")

        # Step 4: Approve Gate 3
        resp_3 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_3["approval_id"],
            "action": "approve",
        })
        self.assertEqual(resp_3.status_code, 200)
        state_3 = resp_3.json()
        self.assertEqual(len(state_3["turns"]), 3)
        self.assertEqual(state_3["turns"][2]["speaker"], "Genesis")
        self.assertEqual(state_3["status"], "COMPLETED")
        self.assertIsNone(state_3["pending_gate"])

    def test_triad_rejection_halts_session(self):
        """Rejecting an approval gate halts the session safely and blocks subsequent dispatches."""
        init_res = self.client.post("/api/chat/triad/initiate", json={
            "probe_prompt": "Test rejection flow.",
            "is_live": False,
        })
        init_data = init_res.json()
        gate_1_id = init_data["pending_gate"]["approval_id"]

        # Reject Gate 1
        reject_res = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_1_id,
            "action": "reject",
        })
        self.assertEqual(reject_res.status_code, 200)
        state = reject_res.json()
        self.assertEqual(state["status"], "CANCELLED_BY_USER")
        self.assertIn("rejected by Fodoh", state["error_message"])
        self.assertIsNone(state["pending_gate"])
        self.assertEqual(len(state["turns"]), 0)

        # Subsequent gate response must fail closed on backend (HTTP 400)
        subsequent_res = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_1_id,
            "action": "approve",
        })
        self.assertEqual(subsequent_res.status_code, 400)
        self.assertIn("already in terminal state", subsequent_res.json()["detail"])

        # Repeated polling of /api/chat/triad/status must be strictly idempotent
        for _ in range(5):
            poll_res = self.client.get("/api/chat/triad/status")
            self.assertEqual(poll_res.status_code, 200)
            poll_data = poll_res.json()
            self.assertEqual(poll_data["status"], "CANCELLED_BY_USER")
            self.assertIsNone(poll_data["pending_gate"])
            self.assertEqual(len(poll_data["turns"]), 0)

    def test_triad_repeated_polling_and_completion_idempotency(self):
        """Verify repeated status polling is idempotent during active, completed, and cancelled states."""
        # Step 1: Initiate
        self.client.post("/api/chat/triad/initiate", json={
            "probe_prompt": "Testing polling idempotency.",
            "is_live": False,
        })

        # Poll repeatedly during Gate 1
        for _ in range(4):
            res = self.client.get("/api/chat/triad/status")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "PENDING_APPROVAL")
            self.assertIsNotNone(data["pending_gate"])
            self.assertEqual(len(data["turns"]), 0)

        gate_1_id = data["pending_gate"]["approval_id"]

        # Step 2: Approve Gate 1
        resp_1 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_1_id,
            "action": "approve",
        })
        self.assertEqual(resp_1.status_code, 200)

        # Poll repeatedly during Gate 2
        for _ in range(4):
            res = self.client.get("/api/chat/triad/status")
            data = res.json()
            self.assertEqual(data["status"], "PENDING_APPROVAL")
            self.assertEqual(len(data["turns"]), 1)
            self.assertEqual(data["turns"][0]["speaker"], "Antigravity")

        gate_2_id = data["pending_gate"]["approval_id"]

        # Step 3: Approve Gate 2
        resp_2 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_2_id,
            "action": "approve",
        })
        self.assertEqual(resp_2.status_code, 200)
        gate_3_id = resp_2.json()["pending_gate"]["approval_id"]

        # Step 4: Approve Gate 3 (Reaches COMPLETED)
        resp_3 = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_3_id,
            "action": "approve",
        })
        self.assertEqual(resp_3.status_code, 200)
        self.assertEqual(resp_3.json()["status"], "COMPLETED")

        # Poll repeatedly after COMPLETED
        for _ in range(5):
            res = self.client.get("/api/chat/triad/status")
            data = res.json()
            self.assertEqual(data["status"], "COMPLETED")
            self.assertIsNone(data["pending_gate"])
            self.assertEqual(len(data["turns"]), 3)

        # Attempting further gate response on COMPLETED session must fail closed (HTTP 400)
        post_completed_res = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": gate_3_id,
            "action": "approve",
        })
        self.assertEqual(post_completed_res.status_code, 400)
        self.assertIn("already in terminal state", post_completed_res.json()["detail"])

    def test_frontend_app_js_event_deduplication_structure(self):
        """Verifies that app.js includes stable event ID tracking and terminal deduplication."""
        app_js_path = PROJECT_ROOT / "interface" / "static" / "app.js"
        self.assertTrue(app_js_path.exists())
        js_code = app_js_path.read_text(encoding="utf-8")
        self.assertIn("renderedEventIds", js_code)
        self.assertIn("data-event-id", js_code)
        self.assertIn("turnEventId", js_code)
        self.assertIn("termEventId", js_code)
        self.assertIn("terminalStatuses", js_code)

    # -------------------------------------------------------------------------
    # 4. Emergency Stop (Kill Switch) & Fail-Closed Safety
    # -------------------------------------------------------------------------

    def test_kill_switch_triggers_and_blocks_requests(self):
        # 1. Trigger emergency stop
        res = self.client.post("/api/kill-switch", json={"action": "trigger"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(engine.is_kill_switch_active)
        self.assertTrue(engine.governor.is_kill_switch_active)

        # Status endpoint must reflect kill_switch_active = True
        status_res = self.client.get("/api/status")
        self.assertEqual(status_res.status_code, 200)
        self.assertTrue(status_res.json()["kill_switch_active"])

        # 2. Subsequent individual message must fail closed (400)
        chat_res = self.client.post("/api/chat/individual", json={
            "recipient": "Genesis",
            "message": "Hello while stopped",
            "is_live": False,
        })
        self.assertEqual(chat_res.status_code, 400)
        self.assertIn("Emergency kill switch is active", chat_res.json()["detail"])

        # 3. Subsequent triad initiation must fail closed (400)
        triad_res = self.client.post("/api/chat/triad/initiate", json={
            "probe_prompt": "Hello while stopped",
            "is_live": False,
        })
        self.assertEqual(triad_res.status_code, 400)
        self.assertIn("Emergency kill switch is active", triad_res.json()["detail"])

        # 4. Subsequent gate response must fail closed (400)
        gate_res = self.client.post("/api/chat/triad/respond-gate", json={
            "approval_id": "nonexistent_gate",
            "action": "approve",
        })
        self.assertEqual(gate_res.status_code, 400)
        self.assertIn("Emergency kill switch is active", gate_res.json()["detail"])

        # 5. Clear emergency stop
        clear_res = self.client.post("/api/kill-switch", json={"action": "clear"})
        self.assertEqual(clear_res.status_code, 200)
        self.assertFalse(engine.is_kill_switch_active)
        self.assertFalse(engine.governor.is_kill_switch_active)

        # Status endpoint must reflect kill_switch_active = False
        status_cleared = self.client.get("/api/status")
        self.assertEqual(status_cleared.status_code, 200)
        self.assertFalse(status_cleared.json()["kill_switch_active"])

        # 6. Operations resume normally
        resume_chat = self.client.post("/api/chat/individual", json={
            "recipient": "Genesis",
            "message": "Hello after clearing stop",
            "is_live": False,
        })
        self.assertEqual(resume_chat.status_code, 200)
        self.assertEqual(resume_chat.json()["speaker"], "Genesis")

    def test_safety_governor_is_kill_switch_active_property(self):
        """Regression test: verify SafetyGovernor.is_kill_switch_active property without monkey patching."""
        gov = engine.governor
        stop_file = gov.stop_file_path

        # Absent flag
        if stop_file.exists():
            stop_file.unlink()
        self.assertFalse(gov.is_kill_switch_active)

        # Present flag
        stop_file.touch()
        try:
            self.assertTrue(gov.is_kill_switch_active)
        finally:
            if stop_file.exists():
                stop_file.unlink()
        self.assertFalse(gov.is_kill_switch_active)

    # -------------------------------------------------------------------------
    # 5. Development Journal Endpoints
    # -------------------------------------------------------------------------

    def test_journal_status_and_report_endpoints(self):
        res_status = self.client.get("/api/journal/status")
        self.assertEqual(res_status.status_code, 200)
        status_data = res_status.json()
        self.assertIn("status", status_data)

        res_report = self.client.get("/api/journal/report")
        self.assertEqual(res_report.status_code, 200)
        report_data = res_report.json()
        self.assertIn("today", report_data)
        self.assertIn("this_week", report_data)
        self.assertIn("cumulative", report_data)

    # -------------------------------------------------------------------------
    # 6. Conversation History & Memory Isolation
    # -------------------------------------------------------------------------

    def test_history_endpoint_and_memory_isolation(self):
        # Send a message
        self.client.post("/api/chat/individual", json={
            "recipient": "Genesis",
            "message": "Testing conversation history isolation",
            "is_live": False,
        })

        hist_res = self.client.get("/api/history")
        self.assertEqual(hist_res.status_code, 200)
        history = hist_res.json()
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[-1]["speaker"], "Genesis")

        # Verify memory/memories.json was NOT modified
        memories_file = PROJECT_ROOT / "memory" / "memories.json"
        if memories_file.exists():
            mem_content = memories_file.read_text()
            self.assertNotIn("Testing conversation history isolation", mem_content)

    # -------------------------------------------------------------------------
    # 7. Startup Initialization & CLI Regression Tests
    # -------------------------------------------------------------------------

    def test_server_run_startup_routine(self):
        """Regression test: verify run() initializes cleanly without NameError (e.g. PROJECT_ROOT)."""
        from interface.server import run
        with patch("uvicorn.run") as mock_uvicorn:
            run(host="127.0.0.1", port=8765)
            self.assertTrue(mock_uvicorn.called)
            _, kwargs = mock_uvicorn.call_args
            self.assertEqual(kwargs.get("host"), "127.0.0.1")
            self.assertEqual(kwargs.get("port"), 8765)

    def test_server_module_invocation_subprocess(self):
        """Regression test: verify python -m interface.server starts up in subprocess without error."""
        import subprocess
        import sys
        res = subprocess.run(
            [
                sys.executable,
                "-c",
                "from unittest.mock import patch; import uvicorn; patch('uvicorn.run').start(); import runpy; runpy.run_module('interface.server', run_name='__main__')",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Server startup failed:\n{res.stderr}")
        self.assertIn("GENESIS COMMUNICATION INTERFACE", res.stdout)
        self.assertIn("Kill Switch:", res.stdout)
        self.assertNotIn("NameError", res.stderr)


    # -------------------------------------------------------------------------
    # 8. Anti-Spoofing & Cost Accounting Regression Tests (Live Triad Issues 1 & 2)
    # -------------------------------------------------------------------------

    def test_anti_spoofing_sanitizes_simulated_agent_turns(self):
        """Regression test for Issue 1: Antigravity Turn 1 output simulating OpenAI-Agent and Genesis."""
        spoofed_output = (
            "### **Antigravity (Proposal)**\n"
            "Implement a Staged Memory Consolidation Pipeline.\n"
            "Instead of writing raw conversation logs directly into long-term storage, route all interactions through a buffer.\n\n"
            "---\n\n"
            "### **OpenAI-Agent (Evaluation & Risk)**\n"
            "Evaluation: A staged consolidation pipeline is an essential optimization.\n"
            "Risk: Automated distillation risks stripping nuanced context.\n\n"
            "---\n\n"
            "### **Genesis (Synthesis & Recommendation)**\n"
            "Recommendation: Prioritize the Staged Memory Pipeline, but pair it with Lossless Provenance Anchors."
        )

        clean_text, detected_spoofs = engine.sanitize_agent_turn_output("Antigravity", spoofed_output)

        # Verify spoofed sections for OpenAI-Agent and Genesis were detected and trimmed
        self.assertIn("OpenAI-Agent", detected_spoofs)
        self.assertIn("Genesis", detected_spoofs)
        self.assertNotIn("Antigravity (Proposal)", clean_text)
        self.assertNotIn("OpenAI-Agent (Evaluation & Risk)", clean_text)
        self.assertNotIn("Genesis (Synthesis & Recommendation)", clean_text)
        self.assertIn("Implement a Staged Memory Consolidation Pipeline", clean_text)
        self.assertIn("route all interactions through a buffer", clean_text)

    def test_anti_spoofing_strips_redundant_self_header(self):
        """Verify self-headers (e.g. ### **OpenAI-Agent**) are stripped without losing text."""
        raw_text = (
            "### **OpenAI-Agent**\n"
            "This is my objective analysis of the proposal."
        )
        clean_text, detected_spoofs = engine.sanitize_agent_turn_output("OpenAI-Agent", raw_text)
        self.assertEqual(detected_spoofs, [])
        self.assertEqual(clean_text, "This is my objective analysis of the proposal.")

    def test_anti_spoofing_preserves_clean_text(self):
        """Verify compliant text without spoofing passes through untouched."""
        clean_input = "Here is my perspective on modular architecture. It ensures loose coupling."
        output, spoofs = engine.sanitize_agent_turn_output("Genesis", clean_input)
        self.assertEqual(spoofs, [])
        self.assertEqual(output, clean_input)

    def test_prompt_directive_injection(self):
        """Verify format_participant_prompt injects multi-agent execution boundary."""
        framed = engine.format_participant_prompt("Antigravity", "Propose an improvement.")
        self.assertIn("[SYSTEM DIRECTIVE — MULTI-AGENT EXECUTION BOUNDARY]", framed)
        self.assertIn("You are speaking strictly as: Antigravity", framed)
        self.assertIn("DO NOT simulate, impersonate, quote, or generate dialogue on behalf of other participants", framed)
        self.assertIn("Propose an improvement.", framed)

    def test_cost_accounting_triad_turn_accumulation(self):
        """Regression test for Issue 2: Triad turns must accumulate into SafetyGovernor and persistent ledger."""
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0)

        # Simulate Triad Turn 1 (Antigravity subscription: $0.00)
        c1 = engine.record_api_spending(
            cost_usd=0.0,
            model="gemini-3.8-flash-high",
            provider="antigravity_subscription",
            source="triad_turn_1",
            session_id="test_sess_01",
        )
        self.assertEqual(c1, 0.0)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0)

        # Simulate Triad Turn 2 (OpenAI-Agent metered: $0.000360)
        c2 = engine.record_api_spending(
            cost_usd=0.000360,
            model="gpt-4o-mini",
            provider="openai_metered",
            source="triad_turn_2",
            session_id="test_sess_01",
        )
        self.assertEqual(c2, 0.000360)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.000360)

        # Simulate Triad Turn 3 (Genesis metered: $0.000316)
        c3 = engine.record_api_spending(
            cost_usd=0.000316,
            model="gpt-4o-mini",
            provider="openai_metered",
            source="triad_turn_3",
            session_id="test_sess_01",
        )
        self.assertEqual(c3, 0.000676)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.000676)

        # Verify ledger file persistence
        self.assertTrue(engine.cost_ledger_file.exists())
        ledger = json.loads(engine.cost_ledger_file.read_text())
        self.assertEqual(len(ledger), 3)
        self.assertEqual(ledger[0]["provider"], "antigravity_subscription")
        self.assertEqual(ledger[1]["provider"], "openai_metered")
        self.assertEqual(ledger[2]["cumulative_cost_usd"], 0.000676)

    def test_cost_accounting_ceiling_exceeded_rejection(self):
        """Verify hard $0.05 limit blocks any transaction exceeding the ceiling."""
        from hub.phase1 import CostLimitExceededError

        # Initial spend under ceiling
        engine.record_api_spending(0.049, "gpt-4o-mini", "openai_metered", "test_source")
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.049)

        # Next spend would push total to 0.051 > 0.050 -> must raise CostLimitExceededError
        with self.assertRaises(CostLimitExceededError) as ctx:
            engine.record_api_spending(0.002, "gpt-4o-mini", "openai_metered", "test_source")

        self.assertIn("exceeding the application estimated cost ceiling", str(ctx.exception))
        # Total should remain at 0.049 without leaking the failed transaction
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.049)

    def test_cost_accounting_concurrent_thread_safety(self):
        """Verify thread-safe locking prevents race conditions under concurrent spend updates."""
        import threading

        threads = []
        increment = 0.0001
        num_threads = 20

        def spend_task():
            engine.record_api_spending(
                cost_usd=increment,
                model="gpt-4o-mini",
                provider="openai_metered",
                source="concurrent_test",
            )

        for _ in range(num_threads):
            t = threading.Thread(target=spend_task)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        expected_total = round(increment * num_threads, 6)
        self.assertEqual(engine.governor.cumulative_cost_usd, expected_total)

    def test_cost_ledger_endpoint_separates_metered_and_subscription(self):
        """Verify /api/costs/ledger returns ledger entries with metered vs subscription distinction."""
        engine.record_api_spending(0.000135, "gpt-4o-mini", "openai_metered", "test_turn")
        engine.record_api_spending(0.0, "gemini-3.8-flash-high", "antigravity_subscription", "test_ag")

        res = self.client.get("/api/costs/ledger")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["max_cost_usd"], 0.05)
        self.assertEqual(data["cumulative_cost_usd"], 0.000135)
        self.assertEqual(data["metered_cost_usd"], 0.000135)
        self.assertEqual(data["ledger_entries_count"], 2)
        self.assertEqual(data["ledger"][0]["provider"], "openai_metered")
        self.assertEqual(data["ledger"][1]["provider"], "antigravity_subscription")

    def test_anti_spoofing_preserves_legitimate_quotes_and_natural_prose(self):
        """Verify quotations, blockquotes, and natural-language statements referencing agents are NOT stripped."""
        text_with_legitimate_references = (
            "Here is my technical assessment of the system:\n\n"
            "> Genesis: \"We should preserve context without creating context bloat.\"\n\n"
            "Genesis recommends that we prioritize staged memory consolidation.\n"
            "As OpenAI-Agent noted earlier, semantic compression loss is a real architectural risk.\n\n"
            "### Genesis Architecture Review\n"
            "The proposed pipeline provides strong modularity and prevents direct writes to long-term memory."
        )

        clean_text, detected_spoofs = engine.sanitize_agent_turn_output("Antigravity", text_with_legitimate_references)

        self.assertEqual(detected_spoofs, [])
        self.assertIn("> Genesis: \"We should preserve context", clean_text)
        self.assertIn("Genesis recommends that we prioritize staged memory consolidation.", clean_text)
        self.assertIn("As OpenAI-Agent noted earlier", clean_text)
        self.assertIn("### Genesis Architecture Review", clean_text)

    def test_pre_dispatch_budget_reservation_blocks_before_execution(self):
        """Verify pre-dispatch reservation blocks an API call before it can be dispatched if it exceeds ceiling."""
        from hub.phase1 import CostLimitExceededError

        # Set spend close to $0.05 ceiling
        engine.governor.cumulative_cost_usd = 0.0495

        # Attempt to reserve $0.0008 (would push to $0.0503 > $0.050)
        with self.assertRaises(CostLimitExceededError) as ctx:
            engine.reserve_api_budget(0.0008, source="test_pre_dispatch")

        self.assertIn("Pre-dispatch budget reservation denied", str(ctx.exception))
        self.assertIn("Call blocked before API dispatch", str(ctx.exception))
        # Verify no reservation was committed
        self.assertEqual(engine.get_active_reservations_total(), 0.0)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0495)

    def test_pre_dispatch_concurrent_reservations_block_overshoot(self):
        """Verify concurrent in-flight reservations accurately block subsequent requests that would breach ceiling."""
        from hub.phase1 import CostLimitExceededError

        engine.governor.cumulative_cost_usd = 0.0490

        # Thread 1 reserves $0.0006 (0.0490 + 0.0006 = 0.0496 <= 0.050) -> succeeds
        res1 = engine.reserve_api_budget(0.0006, source="in_flight_1")
        self.assertEqual(engine.get_active_reservations_total(), 0.0006)

        # Thread 2 attempts to reserve $0.0006 (0.0490 + 0.0006 + 0.0006 = 0.0502 > 0.050) -> must fail!
        with self.assertRaises(CostLimitExceededError) as ctx:
            engine.reserve_api_budget(0.0006, source="in_flight_2")

        self.assertIn("Pre-dispatch budget reservation denied", str(ctx.exception))

        # Settle Thread 1 with actual cost $0.0004
        engine.settle_api_budget(res1, 0.0004, "gpt-4o-mini", "openai_metered", "in_flight_1")
        self.assertEqual(engine.get_active_reservations_total(), 0.0)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0494)

    def test_in_flight_call_exceeding_estimate_settles_accurately(self):
        """Verify that when an in-flight call slightly exceeds its estimate, actual cost is truthfully settled."""
        res_id = engine.reserve_api_budget(0.0005, source="test_underestimate")
        self.assertEqual(engine.get_active_reservations_total(), 0.0005)

        # Actual cost finishes at 0.0007
        final_cost = engine.settle_api_budget(res_id, 0.0007, "gpt-4o-mini", "openai_metered", "test_underestimate")
        self.assertEqual(final_cost, 0.0007)
        self.assertEqual(engine.get_active_reservations_total(), 0.0)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0007)

    def test_release_budget_on_failure(self):
        """Verify reservation is released and budget restored if an operation fails."""
        res_id = engine.reserve_api_budget(0.0008, source="test_failure")
        self.assertEqual(engine.get_active_reservations_total(), 0.0008)

        engine.release_api_budget(res_id)
        self.assertEqual(engine.get_active_reservations_total(), 0.0)
        self.assertEqual(engine.governor.cumulative_cost_usd, 0.0)

    def test_journal_endpoints_active(self):
        """Verify Development Journal status and report endpoints return active session data."""
        res = self.client.get("/api/journal/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertIn("activity", data)

        res_rep = self.client.get("/api/journal/report")
        self.assertEqual(res_rep.status_code, 200)

    def test_html_report_view_and_download_endpoints(self):
        """Verify /report and /api/report/download serve the generated HTML report file."""
        res_view = self.client.get("/report")
        self.assertEqual(res_view.status_code, 200)
        self.assertIn("Genesis Communication Hub", res_view.text)
        self.assertIn("Live Triad Investigation", res_view.text)

        res_dl = self.client.get("/api/report/download")
        self.assertEqual(res_dl.status_code, 200)
        self.assertIn("text/html", res_dl.headers.get("content-type", ""))
        self.assertIn("attachment", res_dl.headers.get("content-disposition", ""))
        self.assertIn("Genesis_Triad_Investigation_and_Hardening_Report.html", res_dl.headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
