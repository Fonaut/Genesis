#!/usr/bin/env python3
"""
Unit tests for Genesis Communication Hub — Phase 1: Three-Agent Collaborative Hub
==================================================================================

Covers offline validation with 100% mocked dependencies:
1. OpenAI-Agent offline behavior, error handling, empty response rejection.
2. Missing API key detection.
3. Full three-agent supervised round with auto-approve.
4. Approval gate rejection at Gate 1, Gate 2, and Gate 3.
5. Fail-closed behavior on empty replies from any participant.
6. Safety Governor turn ceiling and spending cost ceiling.
7. Emergency kill-switch (.hub_stopped) aborts.
8. Memory isolation: assistant dialogue never stored into memories.json or ChromaDB.
9. Privacy: Genesis internal thought excluded from persistent audit logs.
10. ChatGPT summary context briefing ingestion.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from hub.phase1 import (
    CostLimitExceededError,
    HumanApprovalGate,
    HubSafetyError,
    KillSwitchActiveError,
    MockAntigravityAgent,
    OpenAIAgentParticipant,
    Phase1Orchestrator,
    SafetyGovernor,
    TurnLimitReachedError,
)
from hub.phase0 import AgentTurn, GenesisParticipant, SessionAudit


class TestPhase1ThreeAgentHub(unittest.TestCase):
    """Offline test suite for Phase 1 three-agent collaboration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name) / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. OpenAI-Agent Participant Unit Tests
    # -------------------------------------------------------------------------
    def test_openai_agent_offline_mock_mode(self):
        """Verifies deterministic synthetic response in offline mode with zero API calls."""
        agent = OpenAIAgentParticipant(is_live=False)
        reply, meta = agent.process_turn(
            speaker="Antigravity",
            incoming_message="Antigravity technical assessment.",
        )
        self.assertIn("OpenAI research perspective", reply)
        self.assertEqual(meta["speaker"], "OpenAI-Agent")
        self.assertEqual(meta["model"], "synthetic-offline")
        self.assertEqual(meta["cost_usd_est"], 0.0)
        self.assertFalse(meta["is_live"])

    def test_openai_agent_missing_api_key_raises_error(self):
        """When is_live=True and OPENAI_API_KEY is missing, raises HubSafetyError."""
        with patch.dict(os.environ, {}, clear=True):
            agent = OpenAIAgentParticipant(is_live=True)
            with self.assertRaises(HubSafetyError) as ctx:
                agent.process_turn(
                    speaker="Antigravity",
                    incoming_message="Hello",
                )
            self.assertIn("OPENAI_API_KEY is not configured", str(ctx.exception))

    def test_openai_agent_empty_response_raises_error(self):
        """When OpenAI client produces empty content, raises HubSafetyError."""
        agent = OpenAIAgentParticipant(is_live=True, mock_response="")
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "   "  # Whitespace-only
        mock_choice.finish_reason = "stop"
        mock_completion.choices = [mock_choice]
        mock_completion.usage = None
        mock_client.chat.completions.create.return_value = mock_completion

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("openai.OpenAI", return_value=mock_client):
                with self.assertRaises(HubSafetyError) as ctx:
                    agent.process_turn(
                        speaker="Antigravity",
                        incoming_message="Hello",
                    )
                self.assertIn("empty or whitespace-only", str(ctx.exception))

    def test_openai_agent_truncation_detection_and_safeguard(self):
        """When OpenAI API response hits token limit (finish_reason='length'), raises HubSafetyError."""
        agent = OpenAIAgentParticipant(is_live=True, max_tokens=350)
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Establishing a conflict resolution mechanism to address differing opinions constructively could"
        mock_choice.finish_reason = "length"
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock()
        mock_completion.usage.prompt_tokens = 200
        mock_completion.usage.completion_tokens = 350
        mock_completion.usage.total_tokens = 550
        mock_client.chat.completions.create.return_value = mock_completion

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("openai.OpenAI", return_value=mock_client):
                with self.assertRaises(HubSafetyError) as ctx:
                    agent.process_turn(
                        speaker="Antigravity",
                        incoming_message="Analyze division of labor.",
                    )
                self.assertIn("truncated by output token limit", str(ctx.exception))
                self.assertIn("finish_reason='length'", str(ctx.exception))

    def test_openai_agent_normal_completion_metadata(self):
        """When response completes normally (finish_reason='stop'), metadata records safe completion."""
        agent = OpenAIAgentParticipant(is_live=True, max_tokens=800)
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Complete analytical response concluding cleanly."
        mock_choice.finish_reason = "stop"
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock()
        mock_completion.usage.prompt_tokens = 250
        mock_completion.usage.completion_tokens = 150
        mock_completion.usage.total_tokens = 400
        mock_client.chat.completions.create.return_value = mock_completion

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("openai.OpenAI", return_value=mock_client):
                reply, meta = agent.process_turn(
                    speaker="Antigravity",
                    incoming_message="Analyze division of labor.",
                )
                self.assertEqual(reply, "Complete analytical response concluding cleanly.")
                self.assertEqual(meta["finish_reason"], "stop")
                self.assertFalse(meta["is_truncated"])
                self.assertEqual(meta["prompt_tokens_est"], 250)
                self.assertEqual(meta["completion_tokens_est"], 150)
                self.assertEqual(meta["total_tokens_est"], 400)
                self.assertTrue(meta["cost_usd_est"] > 0.0)

    def test_three_agent_exchange_halts_when_openai_agent_is_truncated(self):
        """When OpenAI-Agent is truncated, triad exchange halts before Gate 3, Genesis is never called."""
        ag_agent = MockAntigravityAgent(response_text="Antigravity architecture.")
        truncated_openai = OpenAIAgentParticipant(
            is_live=False,
            mock_response="Incomplete cut off sentence...",
            mock_finish_reason="length",
        )
        genesis = MagicMock(spec=GenesisParticipant)
        genesis.is_live = False
        gate = HumanApprovalGate(auto_approve=True)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=truncated_openai,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with self.assertRaises(HubSafetyError) as ctx:
            orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertIn("truncated", str(ctx.exception))
        genesis.process_turn.assert_not_called()

        # Confirm audit log marked FAILED
        log_files = list(self.log_dir.glob("phase1_*.json"))
        self.assertEqual(len(log_files), 1)
        audit_data = json.loads(log_files[0].read_text())
        self.assertEqual(audit_data["status"], "FAILED")
        self.assertIn("truncated", audit_data["error_message"])

    def test_chatgpt_summary_context_ingestion(self):
        """Verifies that user-provided ChatGPT summary is appended to system prompt."""
        agent = OpenAIAgentParticipant(is_live=True)
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Analysis incorporating ChatGPT discussion."
        mock_completion.usage.prompt_tokens = 100
        mock_completion.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_completion

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}):
            with patch("openai.OpenAI", return_value=mock_client):
                reply, meta = agent.process_turn(
                    speaker="Antigravity",
                    incoming_message="Technical design review.",
                    chatgpt_summary_context="Fodoh and ChatGPT agreed on decoupled memory.",
                )
                self.assertEqual(reply, "Analysis incorporating ChatGPT discussion.")

                # Inspect payload passed to client
                call_args = mock_client.chat.completions.create.call_args[1]
                system_msg = call_args["messages"][0]["content"]
                self.assertIn("Reference Discussion Briefing (provided explicitly by Fodoh):", system_msg)
                self.assertIn("Fodoh and ChatGPT agreed on decoupled memory.", system_msg)

    # -------------------------------------------------------------------------
    # 2. Three-Agent Supervised Orchestration Tests
    # -------------------------------------------------------------------------
    def test_three_agent_supervised_round_mock_completes_and_logs(self):
        """
        Verifies full 3-agent supervised sequence:
        Turn 1: Antigravity
        Turn 2: OpenAI-Agent
        Turn 3: Genesis
        Audit record is persisted and status is COMPLETED.
        """
        ag_agent = MockAntigravityAgent(response_text="Antigravity technical design.")
        openai_agent = OpenAIAgentParticipant(
            is_live=False,
            mock_response="OpenAI-Agent strategic analysis of Antigravity design.",
        )
        genesis = GenesisParticipant(is_live=False)
        gate = HumanApprovalGate(auto_approve=True)
        governor = SafetyGovernor(max_turns=3, max_cost_usd=0.05)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            governor=governor,
            log_dir=self.log_dir,
        )

        audit = orchestrator.execute_triad_poc(
            probe_prompt="Triad collaboration prompt.",
        )

        self.assertEqual(audit.status, "COMPLETED")
        self.assertEqual(len(audit.turns), 3)

        # Check turn order and speakers
        self.assertEqual(audit.turns[0].speaker, "Antigravity")
        self.assertEqual(audit.turns[1].speaker, "OpenAI-Agent")
        self.assertEqual(audit.turns[2].speaker, "Genesis")

        # Confirm contents
        self.assertIn("Antigravity technical design", audit.turns[0].content)
        self.assertIn("OpenAI-Agent strategic analysis", audit.turns[1].content)
        self.assertTrue(len(audit.turns[2].content) > 0)

        # Confirm Genesis internal thought is None in audit
        self.assertIsNone(audit.turns[2].thought)

        # Verify persisted JSON audit record
        log_files = list(self.log_dir.glob("phase1_*.json"))
        self.assertEqual(len(log_files), 1)
        saved_data = json.loads(log_files[0].read_text())
        self.assertEqual(saved_data["status"], "COMPLETED")
        self.assertEqual(len(saved_data["turns"]), 3)
        self.assertIsNone(saved_data["turns"][2]["thought"])

    # -------------------------------------------------------------------------
    # 3. Approval Gate Interception & Cancellation Tests
    # -------------------------------------------------------------------------
    def test_gate_1_rejection_halts_immediately(self):
        """Rejecting Gate 1 aborts session; no turns occur."""
        ag_agent = MockAntigravityAgent()
        openai_agent = OpenAIAgentParticipant(is_live=False)
        genesis = GenesisParticipant(is_live=False)
        gate = HumanApprovalGate(auto_approve=False)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with patch("builtins.input", return_value="n"):
            audit = orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertEqual(audit.status, "CANCELLED_BY_USER")
        self.assertEqual(len(audit.turns), 0)

    def test_gate_2_rejection_halts_before_openai_agent(self):
        """Rejecting Gate 2 aborts after Turn 1; OpenAI-Agent and Genesis are never called."""
        ag_agent = MockAntigravityAgent(response_text="Antigravity turn 1 output.")
        openai_agent = MagicMock(spec=OpenAIAgentParticipant)
        genesis = MagicMock(spec=GenesisParticipant)
        gate = HumanApprovalGate(auto_approve=False)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        # Gate 1: 'y' (approve), Gate 2: 'n' (reject)
        with patch("builtins.input", side_effect=["y", "n"]):
            audit = orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertEqual(audit.status, "CANCELLED_BY_USER")
        self.assertEqual(len(audit.turns), 1)
        self.assertEqual(audit.turns[0].speaker, "Antigravity")
        openai_agent.process_turn.assert_not_called()
        genesis.process_turn.assert_not_called()

    def test_gate_3_rejection_halts_before_genesis(self):
        """Rejecting Gate 3 aborts after Turn 2; Genesis is never called."""
        ag_agent = MockAntigravityAgent(response_text="Antigravity turn 1.")
        openai_agent = OpenAIAgentParticipant(is_live=False, mock_response="OpenAI-Agent turn 2.")
        genesis = MagicMock(spec=GenesisParticipant)
        gate = HumanApprovalGate(auto_approve=False)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        # Gate 1: 'y', Gate 2: 'y', Gate 3: 'n'
        with patch("builtins.input", side_effect=["y", "y", "n"]):
            audit = orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertEqual(audit.status, "CANCELLED_BY_USER")
        self.assertEqual(len(audit.turns), 2)
        self.assertEqual(audit.turns[0].speaker, "Antigravity")
        self.assertEqual(audit.turns[1].speaker, "OpenAI-Agent")
        genesis.process_turn.assert_not_called()

    # -------------------------------------------------------------------------
    # 4. Fail-Closed On Empty Responses
    # -------------------------------------------------------------------------
    def test_antigravity_empty_reply_halts_before_gate_2(self):
        """When Antigravity returns empty reply, exchange halts with FAILED before Gate 2."""
        empty_ag = MockAntigravityAgent(response_text="   ")
        openai_agent = MagicMock(spec=OpenAIAgentParticipant)
        genesis = MagicMock(spec=GenesisParticipant)
        gate = MagicMock(spec=HumanApprovalGate)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=empty_ag,
            openai_agent=openai_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with self.assertRaises(HubSafetyError) as ctx:
            orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertIn("empty or whitespace-only response", str(ctx.exception))
        openai_agent.process_turn.assert_not_called()
        genesis.process_turn.assert_not_called()

    def test_openai_agent_empty_reply_halts_before_gate_3(self):
        """When OpenAI-Agent returns empty reply, exchange halts with FAILED before Gate 3."""
        ag_agent = MockAntigravityAgent(response_text="Valid Antigravity text.")
        empty_openai = OpenAIAgentParticipant(is_live=False, mock_response="   ")
        genesis = MagicMock(spec=GenesisParticipant)
        gate = HumanApprovalGate(auto_approve=True)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=empty_openai,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with self.assertRaises(HubSafetyError) as ctx:
            orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertIn("empty or whitespace-only", str(ctx.exception))
        genesis.process_turn.assert_not_called()

    def test_genesis_empty_reply_halts_and_fails_session(self):
        """When Genesis returns an empty reply, exchange halts with FAILED."""
        ag_agent = MockAntigravityAgent(response_text="Valid Antigravity text.")
        openai_agent = OpenAIAgentParticipant(is_live=False, mock_response="Valid OpenAI-Agent analysis.")
        mock_genesis = MagicMock(spec=GenesisParticipant)
        mock_genesis.process_turn.return_value = ("   ", None, {"speaker": "Genesis", "model": "gpt-4o-mini", "cost_usd_est": 0.0})
        gate = HumanApprovalGate(auto_approve=True)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=mock_genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with self.assertRaises(HubSafetyError) as ctx:
            orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertIn("empty or whitespace-only", str(ctx.exception))
        log_files = list(self.log_dir.glob("phase1_*.json"))
        self.assertEqual(len(log_files), 1)
        audit_data = json.loads(log_files[0].read_text())
        self.assertEqual(audit_data["status"], "FAILED")

    def test_genesis_truncation_detection_and_safeguard(self):
        """When Genesis response hits output token limit (finish_reason='length'), session is marked FAILED."""
        ag_agent = MockAntigravityAgent(response_text="Valid Antigravity text.")
        openai_agent = OpenAIAgentParticipant(is_live=False, mock_response="Valid OpenAI-Agent analysis.")
        mock_genesis = MagicMock(spec=GenesisParticipant)
        mock_genesis.process_turn.return_value = (
            "This practice reinforces continuity and prepares us well for future",
            None,
            {
                "speaker": "Genesis",
                "model": "gpt-4o-mini",
                "finish_reason": "length",
                "is_truncated": True,
                "max_tokens": 800,
                "cost_usd_est": 0.0005,
            },
        )
        gate = HumanApprovalGate(auto_approve=True)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=mock_genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        with self.assertRaises(HubSafetyError) as ctx:
            orchestrator.execute_triad_poc(probe_prompt="Test probe")

        self.assertIn("truncated by output token limit", str(ctx.exception))
        self.assertIn("finish_reason='length'", str(ctx.exception))

        log_files = list(self.log_dir.glob("phase1_*.json"))
        self.assertEqual(len(log_files), 1)
        audit_data = json.loads(log_files[0].read_text())
        self.assertEqual(audit_data["status"], "FAILED")
        self.assertIn("truncated by output token limit", audit_data["error_message"])

    def test_genesis_normal_completion_metadata(self):
        """When Genesis completes cleanly (finish_reason='stop'), session is marked COMPLETED with metadata."""
        ag_agent = MockAntigravityAgent(response_text="Valid Antigravity text.")
        openai_agent = OpenAIAgentParticipant(is_live=False, mock_response="Valid OpenAI-Agent analysis.")
        mock_genesis = MagicMock(spec=GenesisParticipant)
        mock_genesis.process_turn.return_value = (
            "This practice reinforces continuity and prepares us well for future collaboration across our triad.",
            None,
            {
                "speaker": "Genesis",
                "model": "gpt-4o-mini",
                "finish_reason": "stop",
                "is_truncated": False,
                "max_tokens": 800,
                "prompt_tokens": 1050,
                "completion_tokens": 420,
                "total_tokens": 1470,
                "cost_usd_est": 0.00041,
            },
        )
        gate = HumanApprovalGate(auto_approve=True)

        orchestrator = Phase1Orchestrator(
            antigravity_agent=ag_agent,
            openai_agent=openai_agent,
            genesis_participant=mock_genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        audit = orchestrator.execute_triad_poc(probe_prompt="Test probe")
        self.assertEqual(audit.status, "COMPLETED")
        self.assertEqual(len(audit.turns), 3)

        gen_turn = audit.turns[2]
        self.assertEqual(gen_turn.speaker, "Genesis")
        self.assertEqual(gen_turn.metadata["finish_reason"], "stop")
        self.assertFalse(gen_turn.metadata["is_truncated"])
        self.assertEqual(gen_turn.metadata["max_tokens"], 800)
        self.assertEqual(gen_turn.metadata["prompt_tokens"], 1050)
        self.assertEqual(gen_turn.metadata["completion_tokens"], 420)

    def test_llm_ask_with_tools_truncation_details(self):
        """LLM.ask_with_tools accurately returns finish_reason and is_truncated details."""
        from core.llm import LLM
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Truncated text..."
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "length"
        mock_completion.choices = [mock_choice]
        mock_completion.usage.prompt_tokens = 50
        mock_completion.usage.completion_tokens = 250
        mock_completion.usage.total_tokens = 300
        mock_client.chat.completions.create.return_value = mock_completion

        with patch("core.llm.client", mock_client):
            llm = LLM()
            # return_details=False -> string only (backward compat)
            res_str = llm.ask_with_tools("Hello", return_details=False)
            self.assertEqual(res_str, "Truncated text...")

            # return_details=True -> tuple with metadata
            content, details = llm.ask_with_tools("Hello", return_details=True)
            self.assertEqual(content, "Truncated text...")
            self.assertEqual(details["finish_reason"], "length")
            self.assertTrue(details["is_truncated"])
            self.assertEqual(details["usage"]["completion_tokens"], 250)

    # -------------------------------------------------------------------------
    # 5. Safety Governor & Ceilings
    # -------------------------------------------------------------------------
    def test_safety_governor_enforces_turn_ceiling(self):
        """Trips TurnLimitReachedError if turn count exceeds ceiling."""
        gov = SafetyGovernor(max_turns=3)
        gov.record_turn()  # Turn 1
        gov.record_turn()  # Turn 2
        gov.record_turn()  # Turn 3
        with self.assertRaises(TurnLimitReachedError):
            gov.record_turn()  # Turn 4 trips hard ceiling

    def test_safety_governor_enforces_cost_ceiling(self):
        """Trips CostLimitExceededError if cumulative cost crosses ceiling."""
        gov = SafetyGovernor(max_turns=10, max_cost_usd=0.01)
        gov.record_turn(estimated_cost_usd=0.005)
        self.assertAlmostEqual(gov.cumulative_cost_usd, 0.005)
        with self.assertRaises(CostLimitExceededError):
            gov.record_turn(estimated_cost_usd=0.006)  # Crosses 0.01

    def test_emergency_kill_switch_aborts_immediately(self):
        """Detecting .hub_stopped halts execution immediately."""
        stop_file = PROJECT_ROOT / ".hub_stopped"
        stop_file.touch()
        try:
            gov = SafetyGovernor(stop_file=".hub_stopped")
            with self.assertRaises(KillSwitchActiveError):
                gov.check_kill_switch()
        finally:
            if stop_file.exists():
                stop_file.unlink()

    # -------------------------------------------------------------------------
    # 6. Memory Isolation Invariant
    # -------------------------------------------------------------------------
    def test_memory_isolation_in_three_agent_exchange(self):
        """Dialogue from other assistants must never call brain.remember() or store memories."""
        genesis = GenesisParticipant(is_live=False)
        reply, thought, meta = genesis.process_turn(
            speaker="OpenAI-Agent",
            incoming_message="Strategic review from OpenAI-Agent.",
        )
        self.assertFalse(meta.get("memory_stored"))
        self.assertEqual(meta.get("speaker"), "Genesis")


if __name__ == "__main__":
    unittest.main()
