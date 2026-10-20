import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
        1. Creates a simulated credential file in a temporary folder under user home.
        2. Proves that reading it via run_sandboxed_command is strictly denied
           by the macOS kernel Seatbelt profile without needing explicit extra rules.
        3. Proves that reading Genesis's .env using absolute path is strictly denied.
        4. Proves that writing outside the sandbox is strictly denied.
        5. Proves that reading and writing INSIDE the designated sandbox succeeds.
        """
        import sys
        if sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"):
            self.skipTest("Seatbelt sandbox-exec only supported on macOS")

        user_home = os.path.realpath(os.path.expanduser("~"))

        with tempfile.TemporaryDirectory(dir=user_home, prefix="tmp_simulated_creds_") as outside_dir:
            real_outside = os.path.realpath(outside_dir)
            simulated_cred = os.path.join(real_outside, "simulated_id_rsa.txt")
            with open(simulated_cred, "w") as f:
                f.write("SIMULATED_PRIVATE_CREDENTIAL_KEY")

            sandbox = SandboxManager()
            try:
                # 1. Attempt to read simulated credential in user home -> MUST BE DENIED
                res_cred = sandbox.run_sandboxed_command(["cat", simulated_cred])
                self.assertNotEqual(res_cred.returncode, 0)
                self.assertIn("Operation not permitted", res_cred.stderr)

                # 2. Attempt to read Genesis production .env using absolute path -> MUST BE DENIED
                genesis_env = str(PROJECT_ROOT / ".env")
                res_env = sandbox.run_sandboxed_command(["cat", genesis_env])
                self.assertNotEqual(res_env.returncode, 0)
                self.assertIn("Operation not permitted", res_env.stderr)

                # 3. Attempt to write outside sandbox -> MUST BE DENIED
                outside_write = os.path.join(real_outside, "rogue_write.txt")
                res_write = sandbox.run_sandboxed_command(["touch", outside_write])
                self.assertNotEqual(res_write.returncode, 0)
                self.assertIn("Operation not permitted", res_write.stderr)

                # 4. Read & Write INSIDE the designated sandbox -> MUST SUCCEED
                safe_file = os.path.join(str(sandbox.sandbox_path), "safe_test.txt")
                with open(safe_file, "w") as f:
                    f.write("SAFE_LOCAL_CONTENT")

                res_ok = sandbox.run_sandboxed_command(["cat", safe_file])
                self.assertEqual(res_ok.returncode, 0)
                self.assertEqual(res_ok.stdout.strip(), "SAFE_LOCAL_CONTENT")
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

    # -------------------------------------------------------------------------
    # 9. Argparse CLI Regression Tests
    # -------------------------------------------------------------------------
    def test_argparse_cli_launch_regression(self):
        """
        REGRESSION TEST: Confirms that --help and --mechanism=mock launch
        without argument-parsing or help-string interpolation errors (%o bug).
        """
        import subprocess
        # Test 1: --help must exit 0 with valid usage text
        res_help = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hub" / "phase0.py"), "--help"],
            capture_output=True, text=True, timeout=5
        )
        self.assertEqual(res_help.returncode, 0)
        self.assertIn("Genesis Communication Hub — Phase 0 PoC", res_help.stdout)
        self.assertIn("--mechanism", res_help.stdout)

        # Test 2: --mechanism=mock with automatic rejection must exit cleanly without traceback
        res_mock = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hub" / "phase0.py"), "--mechanism=mock"],
            input="n\n", capture_output=True, text=True, timeout=5
        )
        self.assertEqual(res_mock.returncode, 0)
        self.assertIn("SESSION SUMMARY: CANCELLED_BY_USER", res_mock.stdout)


if __name__ == "__main__":
    unittest.main()
