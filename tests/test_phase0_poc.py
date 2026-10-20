import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hub.phase0 import (
    AgentTurn,
    CliAntigravityAgent,
    ExecutionCancelledError,
    GenesisParticipant,
    HumanApprovalGate,
    HubSafetyError,
    KillSwitchActiveError,
    MockAntigravityAgent,
    Phase0Orchestrator,
    SafetyGovernor,
    SandboxManager,
    TurnLimitReachedError,
)


class TestPhase0HubEngine(unittest.TestCase):
    """Offline unit tests for Phase 0 PoC isolation, governance, and memory rules."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name) / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Sandbox & Filesystem Isolation Tests
    # -------------------------------------------------------------------------
    def test_sandbox_isolation_and_env_sanitization(self):
        """Verifies sandbox is completely isolated and sensitive credentials are scrubbed."""
        # Inject fake secrets into environment
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-secret-fake-key",
            "GEMINI_API_KEY": "AIzaSy-fake-secret",
            "PRIVATE_TOKEN": "token-12345",
            "SAFE_VAR": "visible_safe_value",
        }):
            sandbox = SandboxManager()
            try:
                # 1. Path isolation check
                self.assertTrue(sandbox.sandbox_path.exists())
                self.assertFalse(sandbox.verify_isolation(Path("/Users/fodohadamah/Desktop/Genesis/memory/memories.json")))
                self.assertFalse(sandbox.verify_isolation(Path("/Users/fodohadamah/Desktop/Genesis/identity/self.json")))
                self.assertFalse(sandbox.verify_isolation(Path("/Users/fodohadamah/Desktop/Genesis/.env")))

                # 2. Environment sanitization check
                clean_env = sandbox.get_sanitized_env()
                self.assertNotIn("OPENAI_API_KEY", clean_env)
                self.assertNotIn("GEMINI_API_KEY", clean_env)
                self.assertNotIn("PRIVATE_TOKEN", clean_env)
                self.assertEqual(clean_env.get("SAFE_VAR"), "visible_safe_value")
                self.assertEqual(clean_env.get("GENESIS_SANDBOX"), "1")
            finally:
                sandbox.cleanup()

    # -------------------------------------------------------------------------
    # 2. Human Approval Gate Tests
    # -------------------------------------------------------------------------
    def test_approval_gate_rejection_halts_immediately(self):
        """Verifies that if Fodoh types 'n' or rejects, ExecutionCancelledError is raised."""
        gate = HumanApprovalGate(auto_approve=False)

        with patch("builtins.input", return_value="n"):
            with self.assertRaises(ExecutionCancelledError) as ctx:
                gate.request_approval("Send Probe", "Test content", "Antigravity")
            self.assertIn("rejected by Fodoh", str(ctx.exception))

        with patch("builtins.input", return_value=""):
            with self.assertRaises(ExecutionCancelledError):
                gate.request_approval("Send Probe", "Test content", "Antigravity")

    def test_approval_gate_acceptance(self):
        """Verifies that typing 'y' authorizes the turn."""
        gate = HumanApprovalGate(auto_approve=False)
        with patch("builtins.input", return_value="y"):
            authorized = gate.request_approval("Send Probe", "Test content", "Antigravity")
            self.assertTrue(authorized)

    # -------------------------------------------------------------------------
    # 3. Governance & Kill Switch Tests
    # -------------------------------------------------------------------------
    def test_kill_switch_active_aborts_immediately(self):
        """Verifies that an emergency stop file halts the orchestrator before execution."""
        stop_file = Path(self.temp_dir.name) / ".hub_stopped"
        stop_file.touch()

        governor = SafetyGovernor(max_turns=2, stop_file=str(stop_file))
        with self.assertRaises(KillSwitchActiveError):
            governor.check_kill_switch()

    def test_hard_turn_ceiling_enforcement(self):
        """Verifies that exceeding the MAX_TURNS ceiling raises TurnLimitReachedError."""
        governor = SafetyGovernor(max_turns=2, stop_file=".nonexistent_stop_file")
        governor.record_turn()  # Turn 1
        governor.record_turn()  # Turn 2

        with self.assertRaises(TurnLimitReachedError):
            governor.record_turn()  # Turn 3 (trips limit)

    # -------------------------------------------------------------------------
    # 4. Strict Memory Isolation Tests
    # -------------------------------------------------------------------------
    def test_memory_isolation_invariant_for_assistant_turns(self):
        """
        STRICT REQUIREMENT: Dialogue from Antigravity must NEVER be saved
        into Fodoh's personal long-term memory (memories.json or ChromaDB).
        """
        mock_llm = MagicMock()
        mock_llm.think.return_value = "Private Genesis thought"
        mock_llm.ask_with_tools.return_value = "Genesis reply to Antigravity"

        genesis = GenesisParticipant(mock_llm=mock_llm)
        reply, thought, meta = genesis.process_turn(
            speaker="Antigravity",
            incoming_message="Antigravity technical assessment of memory architectures.",
        )

        self.assertIn("Antigravity", reply)
        self.assertIn("Reflecting on Antigravity's insights regarding memory vs history.", thought)
        self.assertFalse(meta.get("memory_stored"))

    # -------------------------------------------------------------------------
    # 5. End-to-End Orchestrator PoC (Offline Mock)
    # -------------------------------------------------------------------------
    def test_offline_mock_poc_exchange_completes_and_logs(self):
        """Verifies full execution of Phase 0 with Mock Antigravity agent and audit persistence."""
        mock_agent = MockAntigravityAgent()
        gate = HumanApprovalGate(auto_approve=True)  # Auto-approve for automated unittest

        mock_llm = MagicMock()
        mock_llm.think.return_value = "Genesis thinking"
        mock_llm.ask_with_tools.return_value = "Genesis reply"
        genesis = GenesisParticipant(mock_llm=mock_llm)

        orchestrator = Phase0Orchestrator(
            antigravity_agent=mock_agent,
            genesis_participant=genesis,
            approval_gate=gate,
            log_dir=self.log_dir,
        )

        audit = orchestrator.execute_poc(probe_prompt="Testing architectural memory separation")

        self.assertEqual(audit.status, "COMPLETED")
        self.assertEqual(len(audit.turns), 2)
        self.assertEqual(audit.turns[0].speaker, "Antigravity")
        self.assertEqual(audit.turns[1].speaker, "Genesis")
        self.assertTrue(audit.total_tokens_estimated > 0)

        # Verify JSON audit record was written
        log_files = list(self.log_dir.glob("phase0_*.json"))
        self.assertEqual(len(log_files), 1)
        saved_audit = json.loads(log_files[0].read_text())
        self.assertEqual(saved_audit["status"], "COMPLETED")
        self.assertEqual(len(saved_audit["turns"]), 2)

    # -------------------------------------------------------------------------
    # 6. CLI Missing Binary Failsafe
    # -------------------------------------------------------------------------
    def test_cli_agent_failsafe_when_agy_not_installed(self):
        """Verifies that CliAntigravityAgent safely raises HubSafetyError when binary is missing."""
        cli_agent = CliAntigravityAgent(cli_path="nonexistent_agy_binary_xyz")
        sandbox = SandboxManager()
        try:
            with self.assertRaises(HubSafetyError) as ctx:
                cli_agent.send_probe("Test prompt", sandbox)
            self.assertIn("is not installed or not in PATH", str(ctx.exception))
        finally:
            sandbox.cleanup()

    # -------------------------------------------------------------------------
    # 7. macOS Seatbelt OS-Level Negative Access Test
    # -------------------------------------------------------------------------
    def test_seatbelt_negative_access_denies_outside_file(self):
        """
        HARMLESS NEGATIVE-ACCESS TEST:
        Creates a synthetic private file outside the sandbox.
        Proves that reading it via run_sandboxed_command is strictly denied
        by the macOS kernel Seatbelt profile (EPERM / Operation not permitted).
        """
        import sys
        if sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"):
            self.skipTest("Seatbelt sandbox-exec only supported on macOS")

        with tempfile.TemporaryDirectory() as outside_dir:
            real_outside = os.path.realpath(outside_dir)
            synthetic_secret = os.path.join(real_outside, "synthetic_fictional_private.txt")
            with open(synthetic_secret, "w") as f:
                f.write("SYNTHETIC_DATA_CONFIDENTIAL")

            sandbox = SandboxManager()
            try:
                # Attempt to read synthetic file outside sandbox using absolute path
                res = sandbox.run_sandboxed_command(
                    ["cat", synthetic_secret],
                    extra_denied_paths=[Path(synthetic_secret)],
                )
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Operation not permitted", res.stderr)
            finally:
                sandbox.cleanup()

    # -------------------------------------------------------------------------
    # 8. Guaranteed Offline Mode (Zero Paid API Calls)
    # -------------------------------------------------------------------------
    def test_offline_mode_guarantees_zero_live_api_calls(self):
        """Verifies that GenesisParticipant in offline mode operates with zero live LLM calls."""
        genesis = GenesisParticipant(is_live=False)
        self.assertIsNone(genesis.ai)
        self.assertIsNone(genesis.planner)
        self.assertIsNone(genesis.brain)

        reply, thought, meta = genesis.process_turn(
            speaker="Antigravity",
            incoming_message="What is our architecture?",
        )
        self.assertIn("Antigravity", reply)
        self.assertIn("Offline dry-run", thought)
        self.assertFalse(meta.get("is_live"))
        self.assertFalse(meta.get("memory_stored"))


if __name__ == "__main__":
    unittest.main()
