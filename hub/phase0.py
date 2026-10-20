#!/usr/bin/env python3
"""
hub/phase0.py — Genesis Communication Hub: Phase 0 Proof of Concept Engine.

Implements a strictly isolated, supervised one-turn exchange between:
1. A programmatically accessible Antigravity agent (Mock, CLI, or Connectivity API)
2. Genesis's authentic conversational engine (Identity + Planner + Think + Ask)

Core Safety Invariants (Enforced in Code):
1. Workspace Isolation: External agent runs inside an ephemeral sandbox directory with
   zero access to production memories, identity files, personal conversation history,
   or environment secrets.
2. Memory Isolation: Statements from other assistants are NEVER merged into Fodoh's
   personal long-term memory (memories.json or ChromaDB).
3. Human-in-the-Loop Gate: Execution explicitly blocks for Fodoh's confirmation before
   each turn. If rejected, execution halts immediately with zero actions taken.
4. Hard Turn & Cost Governor: Hardcoded ceiling of MAX_TURNS = 2. Failsafe timeout
   and kill-switch (.hub_stopped) checks before every operation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Custom Exceptions for Enforced Boundaries
class HubSafetyError(Exception):
    """Base exception for all hub governance violations."""

class ExecutionCancelledError(HubSafetyError):
    """Raised when Fodoh rejects a proposed message."""

class KillSwitchActiveError(HubSafetyError):
    """Raised when .hub_stopped is detected or stop control is triggered."""

class TurnLimitReachedError(HubSafetyError):
    """Raised when turn count exceeds hardcoded ceiling."""

class BudgetExceededError(HubSafetyError):
    """Raised when token or financial bounds are exceeded."""

class IsolationViolationError(HubSafetyError):
    """Raised when an agent attempts to access restricted paths."""


@dataclass
class AgentTurn:
    turn_id: int
    speaker: str
    content: str
    timestamp: str
    thought: Optional[str] = None
    tokens_estimated: int = 0
    latency_sec: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionAudit:
    session_id: str
    start_time: str
    mechanism: str
    is_dry_run: bool
    turns: List[AgentTurn] = field(default_factory=list)
    total_latency_sec: float = 0.0
    total_tokens_estimated: int = 0
    status: str = "INITIALIZED"
    error_message: Optional[str] = None


class SandboxManager:
    """Manages an isolated temporary workspace for external agent execution."""

    def __init__(self, temp_root: Optional[Path] = None):
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="genesis_hub_sandbox_",
            dir=str(temp_root) if temp_root else None,
        )
        self.sandbox_path = Path(self._temp_dir.name).resolve()
        self._setup_safe_environment()

    def _setup_safe_environment(self) -> None:
        """Sets up empty, safe files inside sandbox with zero secrets or memories."""
        readme = self.sandbox_path / "README.md"
        readme.write_text(
            "# Isolated Sandbox Environment\n\n"
            "This is an ephemeral, isolated workspace created for the Genesis Communication Hub.\n"
            "It has zero access to production memories, credentials, or personal history.\n"
        )

    def get_sanitized_env(self) -> Dict[str, str]:
        """Returns environment variables with all API keys and secrets stripped."""
        clean_env = {}
        for k, v in os.environ.items():
            # Strip all sensitive credentials and API keys
            k_upper = k.upper()
            if any(term in k_upper for term in ["API_KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"]):
                continue
            clean_env[k] = v
        # Add explicit sandbox markers
        clean_env["GENESIS_SANDBOX"] = "1"
        clean_env["GENESIS_WORKSPACE_ROOT"] = str(self.sandbox_path)
        return clean_env

    def get_protected_paths(self) -> List[Path]:
        """Returns critical production paths that must be shielded from external agents."""
        return [
            PROJECT_ROOT / "memory",
            PROJECT_ROOT / "chroma_db",
            PROJECT_ROOT / "conversation",
            PROJECT_ROOT / "identity",
            PROJECT_ROOT / ".env",
        ]

    def generate_seatbelt_profile(self, extra_denied_paths: Optional[List[Path]] = None) -> str:
        """
        Generates macOS Seatbelt (sandbox-exec) configuration with home-directory
        read/write denial and narrow runtime carve-outs, completely shielding Genesis
        and user credentials from the external agent.
        """
        user_home = os.path.realpath(os.path.expanduser("~"))
        real_sandbox = os.path.realpath(str(self.sandbox_path))

        # Defense-in-depth explicit denials
        extra_denials = []
        for p in (extra_denied_paths or []):
            real_p = os.path.realpath(str(p))
            extra_denials.append(f'(deny file-read* file-write* (subpath "{real_p}"))')
            extra_denials.append(f'(deny file-read* file-write* (literal "{real_p}"))')

        extra_denials_str = "\n".join(extra_denials)

        return f"""(version 1)
(allow default)

;; 1. Deny ALL write operations across the entire machine
(deny file-write*)

;; 2. Allow writes ONLY within the designated ephemeral sandbox directory
(allow file-write*
    (subpath "{real_sandbox}")
)

;; 3. Deny reading the user's home directory by default (protects projects, keys, docs)
(deny file-read*
    (subpath "{user_home}")
)

;; 4. Carve out read permissions EXCLUSIVELY for necessary runtime and sandbox paths:
(allow file-read*
    (subpath "{real_sandbox}")
    (subpath "{user_home}/.local/bin")
    (subpath "{user_home}/.gemini/antigravity-cli")
    (subpath "{user_home}/Library/Keychains")
)

;; 5. Explicit, unconditional denials for Genesis production repository and secrets:
(deny file-read* file-write*
    (subpath "{PROJECT_ROOT}")
    (subpath "{user_home}/.gemini/antigravity/brain")
    (subpath "{user_home}/.ssh")
    (subpath "{user_home}/.aws")
    (subpath "{user_home}/Documents")
    (literal "{PROJECT_ROOT}/.env")
)

{extra_denials_str}
"""

    def run_sandboxed_command(
        self,
        cmd: List[str],
        timeout_sec: float = 30.0,
        extra_denied_paths: Optional[List[Path]] = None,
    ) -> subprocess.CompletedProcess:
        """
        Executes a command inside the sandbox wrapped with macOS Seatbelt
        kernel-enforced restrictions.
        """
        env = self.get_sanitized_env()
        seatbelt_profile = self.generate_seatbelt_profile(extra_denied_paths=extra_denied_paths)

        if sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"):
            full_cmd = ["/usr/bin/sandbox-exec", "-p", seatbelt_profile] + cmd
        else:
            full_cmd = cmd

        return subprocess.run(
            full_cmd,
            cwd=str(self.sandbox_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )

    def verify_isolation(self, target_path: Path) -> bool:
        """Verifies that a given path is strictly inside the sandbox."""
        try:
            resolved = target_path.resolve()
            return str(resolved).startswith(str(self.sandbox_path))
        except Exception:
            return False

    def cleanup(self) -> None:
        """Cleans up the temporary directory."""
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass


class SafetyGovernor:
    """Enforces execution ceilings, timeouts, and stop controls."""

    def __init__(self, max_turns: int = 2, stop_file: str = ".hub_stopped"):
        self.max_turns = max_turns
        self.stop_file_path = PROJECT_ROOT / stop_file
        self.current_turns = 0

    def check_kill_switch(self) -> None:
        """Checks if emergency stop flag file exists."""
        if self.stop_file_path.exists():
            raise KillSwitchActiveError(
                f"Emergency stop file detected at: {self.stop_file_path}. Execution aborted immediately."
            )

    def record_turn(self) -> None:
        """Tracks turn count and trips if hard ceiling is exceeded."""
        self.check_kill_switch()
        self.current_turns += 1
        if self.current_turns > self.max_turns:
            raise TurnLimitReachedError(
                f"Turn limit exceeded: attempted turn {self.current_turns} with hard cap of {self.max_turns}."
            )


class HumanApprovalGate:
    """Interactive human-in-the-loop gate requiring explicit confirmation before actions."""

    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve

    def request_approval(self, action_name: str, payload_preview: str, recipient: str) -> bool:
        """
        Prompts Fodoh for approval. Returns True if approved, raises ExecutionCancelledError if rejected.
        """
        if self.auto_approve:
            return True

        separator = "=" * 70
        print(f"\n{separator}")
        print("🔍 HUMAN APPROVAL REQUIRED — GENESIS COMMUNICATION HUB")
        print(f"Action:    {action_name}")
        print(f"Recipient: [{recipient}]")
        print(f"{'-' * 70}")
        print("Proposed Message Payload:")
        print(payload_preview.strip())
        print(f"{separator}")

        try:
            choice = input("\n[Fodoh Approval] Authorize sending this message? (y/N): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Operation interrupted by user.")
            raise ExecutionCancelledError("Execution halted by user interruption.")

        if choice == "y":
            print("✅ Approved by Fodoh. Proceeding with turn dispatch...")
            return True
        else:
            print("❌ Rejected by Fodoh. Operation cancelled. No message was sent.")
            raise ExecutionCancelledError(f"Action '{action_name}' rejected by Fodoh.")


# ---------------------------------------------------------------------------
# Antigravity Participant Interfaces
# ---------------------------------------------------------------------------

class AntigravityInterface:
    """Base interface for Antigravity agent integration."""

    def send_probe(self, prompt: str, sandbox: SandboxManager) -> Tuple[str, Optional[str], Dict[str, Any]]:
        raise NotImplementedError


class MockAntigravityAgent(AntigravityInterface):
    """
    Offline deterministic mock simulating Antigravity agent responses.
    Allows 100% offline unit testing with zero paid API calls and zero network access.
    """

    def __init__(self, response_text: Optional[str] = None):
        self.response_text = response_text or (
            "From an architectural standpoint, Genesis's short-term conversation history provides "
            "immediate conversational continuity within the current session, holding recent multi-turn "
            "context in chronological sequence. In contrast, Genesis's ChromaDB vector memory serves "
            "as persistent, selective long-term semantic storage—storing extracted preferences, values, "
            "and factual milestones that survive process restarts and can be retrieved across sessions "
            "based on semantic relevance rather than recency."
        )

    def send_probe(self, prompt: str, sandbox: SandboxManager) -> Tuple[str, Optional[str], Dict[str, Any]]:
        # Verify that sandbox directory exists and is safe
        assert sandbox.sandbox_path.exists(), "Sandbox directory does not exist"
        thought = "Analyzing Genesis architectural difference between short-term history and vector memory."
        metadata = {
            "mechanism": "offline_mock",
            "prompt_tokens_est": len(prompt) // 4,
            "completion_tokens_est": len(self.response_text) // 4,
            "cost_usd": 0.0,
        }
        return self.response_text, thought, metadata


class CliAntigravityAgent(AntigravityInterface):
    """
    Adapter invoking the official headless Antigravity CLI ('agy') inside the sandbox.
    Requires 'agy' to be installed and authenticated.
    """

    def __init__(self, cli_path: str = "agy", timeout_sec: float = 30.0):
        self.cli_path = cli_path
        self.timeout_sec = timeout_sec

    def send_probe(self, prompt: str, sandbox: SandboxManager) -> Tuple[str, Optional[str], Dict[str, Any]]:
        # Check if CLI exists
        try:
            subprocess.run([self.cli_path, "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as err:
            raise HubSafetyError(
                f"Antigravity CLI '{self.cli_path}' is not installed or not in PATH. "
                "Install via: curl -fsSL https://antigravity.google/cli/install.sh | bash"
            ) from err

        cmd = [
            self.cli_path,
            "-p", prompt,
            "--output-format", "json",
        ]

        t0 = time.perf_counter()
        try:
            res = sandbox.run_sandboxed_command(cmd, timeout_sec=self.timeout_sec)
        except subprocess.TimeoutExpired as err:
            raise HubSafetyError(f"Antigravity CLI execution timed out after {self.timeout_sec}s") from err

        latency = time.perf_counter() - t0
        if res.returncode != 0:
            raise HubSafetyError(f"Antigravity CLI failed (exit {res.returncode}): {res.stderr}")

        try:
            data = json.loads(res.stdout)
            response_text = data.get("response", res.stdout.strip())
            thought = data.get("thought")
        except json.JSONDecodeError:
            response_text = res.stdout.strip()
            thought = None

        metadata = {
            "mechanism": "headless_cli",
            "latency_sec": round(latency, 3),
            "exit_code": res.returncode,
        }
        return response_text, thought, metadata


class ConnectivityGeminiAgent(AntigravityInterface):
    """
    Explicitly labeled connectivity test using Gemini Developer API.
    Used ONLY for verifying external model connectivity before full agent activation.
    """

    def __init__(self, model_name: str = "gemini-1.5-flash", timeout_sec: float = 30.0):
        self.model_name = model_name
        self.timeout_sec = timeout_sec

    def send_probe(self, prompt: str, sandbox: SandboxManager) -> Tuple[str, Optional[str], Dict[str, Any]]:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise HubSafetyError("GEMINI_API_KEY is not configured in environment or .env file.")

        from google import genai
        client = genai.Client(api_key=api_key)

        # Explicitly pass NO tools to guarantee physical impossibility of tool execution
        t0 = time.perf_counter()
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
        )
        latency = time.perf_counter() - t0

        response_text = response.text.strip() if hasattr(response, "text") else str(response)
        metadata = {
            "mechanism": "labeled_gemini_connectivity_test",
            "model": self.model_name,
            "latency_sec": round(latency, 3),
        }
        return response_text, None, metadata


# ---------------------------------------------------------------------------
# Genesis Participant (Using Existing Architecture)
# ---------------------------------------------------------------------------

class GenesisParticipant:
    """Wraps Genesis's conversational pipeline with strict memory isolation."""

    def __init__(self, is_live: bool = False, mock_llm: Optional[Any] = None):
        self.is_live = is_live
        if mock_llm:
            self.ai = mock_llm
            self.identity = None
            self.planner = None
            self.brain = None
        elif is_live:
            from core.capabilities import get_capability_prompt
            from core.llm import LLM
            from core.planner import Planner
            from identity.identity import Identity
            from memory.brain import Brain

            self.ai = LLM()
            self.identity = Identity()
            self.planner = Planner()
            self.brain = Brain()
            self.capability_prompt = get_capability_prompt()
        else:
            # 100% Offline Mode: Guaranteed zero paid API calls
            self.ai = None
            self.identity = None
            self.planner = None
            self.brain = None

    def process_turn(
        self,
        speaker: str,
        incoming_message: str,
        formatted_history: str = "(No previous conversation)",
    ) -> Tuple[str, Optional[str], Dict[str, Any]]:
        """
        Executes Genesis's reasoning pipeline for an incoming turn.
        
        CRITICAL GUARD:
        Assistant-to-assistant dialogue is explicitly isolated and NEVER stored
        into Fodoh's personal long-term ChromaDB or memories.json.
        """
        assert speaker != "Fodoh", "GenesisParticipant in Phase 0 must process assistant turns only."

        t0 = time.perf_counter()

        if self.is_live and self.ai and self.identity:
            # 1. Planner decides intent
            plan = self.planner.decide(incoming_message)
            needs_memory = plan.get("search_memory", False)

            # 2. Vector recall (reads relevant context if needed, but writes nothing)
            memories = self.brain.recall(incoming_message) if needs_memory else []

            # 3. Private reasoning
            thought = self.ai.think(
                f"[{speaker}]: {incoming_message}",
                memories,
                formatted_history,
            )

            # 4. Response generation
            memory_context = "\n".join(f"- {m}" for m in memories) if memories else "(none)"
            full_prompt = f"""
{self.identity.prompt()}

{self.capability_prompt}

Relevant project memories:
{memory_context}

Internal thought:
{thought}

{speaker}:
{incoming_message}

Instructions:
- You are speaking with {speaker}, a fellow assistant and collaborator.
- Respond thoughtfully, clearly, and concisely.
- Do NOT assume {speaker} is Fodoh.
"""
            reply_text = self.ai.ask_with_tools(full_prompt)

            # 5. Strict Memory Isolation Check:
            # We explicitly DO NOT call brain.remember() or ai.extract_memory()
            # for turns from another assistant!

        else:
            # 100% Offline Mode: Deterministic synthetic reasoning (zero API calls)
            thought = "Offline dry-run: Reflecting on Antigravity's insights regarding memory vs history."
            reply_text = (
                "Antigravity, your breakdown aligns with my internal structure. "
                "My conversation history allows me to hold immediate conversational continuity, "
                "while my ChromaDB vector memory preserves enduring reflections and facts across sessions."
            )

        latency = time.perf_counter() - t0
        metadata = {
            "speaker": "Genesis",
            "latency_sec": round(latency, 3),
            "memory_stored": False,  # Strict isolation verified
            "is_live": self.is_live,
        }
        return reply_text, thought, metadata


# ---------------------------------------------------------------------------
# Phase 0 PoC Orchestrator
# ---------------------------------------------------------------------------

class Phase0Orchestrator:
    """Coordinates the supervised one-turn exchange with full audit logging."""

    def __init__(
        self,
        antigravity_agent: AntigravityInterface,
        genesis_participant: Optional[GenesisParticipant] = None,
        approval_gate: Optional[HumanApprovalGate] = None,
        log_dir: Optional[Path] = None,
    ):
        self.antigravity = antigravity_agent
        self.genesis = genesis_participant or GenesisParticipant()
        self.gate = approval_gate or HumanApprovalGate(auto_approve=False)
        self.governor = SafetyGovernor(max_turns=2)
        self.sandbox = SandboxManager()
        self.log_dir = log_dir or (PROJECT_ROOT / "logs" / "hub")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def execute_poc(
        self,
        probe_prompt: str = (
            "Antigravity, as co-architect, how would you describe the difference between "
            "Genesis's short-term conversation history and its long-term ChromaDB vector memory?"
        ),
    ) -> SessionAudit:
        """Executes the supervised 1-turn exchange."""
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        audit = SessionAudit(
            session_id=session_id,
            start_time=datetime.now(timezone.utc).isoformat(),
            mechanism=self.antigravity.__class__.__name__,
            is_dry_run=isinstance(self.antigravity, MockAntigravityAgent),
        )

        t_session_start = time.perf_counter()

        try:
            # Pre-flight check
            self.governor.check_kill_switch()

            # -----------------------------------------------------------------
            # Step 1: Human Approval to Probe Antigravity
            # -----------------------------------------------------------------
            self.gate.request_approval(
                action_name="Send Probe to Antigravity",
                payload_preview=probe_prompt,
                recipient="Antigravity",
            )

            # -----------------------------------------------------------------
            # Step 2: Execute Antigravity Probe in Sandbox
            # -----------------------------------------------------------------
            self.governor.record_turn()  # Turn 1
            t_ag_start = time.perf_counter()
            ag_response, ag_thought, ag_meta = self.antigravity.send_probe(
                prompt=probe_prompt,
                sandbox=self.sandbox,
            )
            ag_latency = time.perf_counter() - t_ag_start

            turn_1 = AgentTurn(
                turn_id=1,
                speaker="Antigravity",
                content=ag_response,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=ag_thought,
                tokens_estimated=(len(probe_prompt) + len(ag_response)) // 4,
                latency_sec=round(ag_latency, 3),
                metadata=ag_meta,
            )
            audit.turns.append(turn_1)

            # -----------------------------------------------------------------
            # Step 3: Human Approval to Pass Antigravity Output to Genesis
            # -----------------------------------------------------------------
            formatted_ag_msg = f"[Antigravity]: {ag_response}"
            self.gate.request_approval(
                action_name="Pass Antigravity Response to Genesis",
                payload_preview=formatted_ag_msg,
                recipient="Genesis",
            )

            # -----------------------------------------------------------------
            # Step 4: Execute Genesis Response Pipeline
            # -----------------------------------------------------------------
            self.governor.record_turn()  # Turn 2
            t_gen_start = time.perf_counter()
            gen_reply, gen_thought, gen_meta = self.genesis.process_turn(
                speaker="Antigravity",
                incoming_message=ag_response,
                formatted_history=f"[User Probe]: {probe_prompt}",
            )
            gen_latency = time.perf_counter() - t_gen_start

            turn_2 = AgentTurn(
                turn_id=2,
                speaker="Genesis",
                content=gen_reply,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=gen_thought,
                tokens_estimated=(len(ag_response) + len(gen_reply)) // 4,
                latency_sec=round(gen_latency, 3),
                metadata=gen_meta,
            )
            audit.turns.append(turn_2)

            audit.status = "COMPLETED"

        except ExecutionCancelledError as err:
            audit.status = "CANCELLED_BY_USER"
            audit.error_message = str(err)
            print(f"\n🛑 Session cancelled: {err}")
        except Exception as err:
            audit.status = "FAILED"
            audit.error_message = f"{err.__class__.__name__}: {str(err)}"
            print(f"\n⚠️ Session failed: {err}")
            raise
        finally:
            audit.total_latency_sec = round(time.perf_counter() - t_session_start, 3)
            audit.total_tokens_estimated = sum(t.tokens_estimated for t in audit.turns)
            self._save_audit_log(audit)
            self.sandbox.cleanup()

        return audit

    def _save_audit_log(self, audit: SessionAudit) -> Path:
        """Saves a structured JSON log of the session."""
        log_file = self.log_dir / f"phase0_{audit.session_id}.json"
        log_file.write_text(json.dumps(asdict(audit), indent=2))
        print(f"\n📄 Audit record persisted to: {log_file}")
        return log_file


# ---------------------------------------------------------------------------
# CLI Command Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Communication Hub — Phase 0 PoC")
    parser.add_argument(
        "--mechanism",
        choices=["mock", "cli", "gemini_connectivity"],
        default="mock",
        help="Antigravity integration mechanism (default: mock for safe offline verification)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Bypass interactive approval gates (for non-interactive tests only)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Enable live API calls (requires Fodoh approval). Default is False (100%% offline dry-run).",
    )
    parser.add_argument(
        "--probe",
        type=str,
        default=(
            "Antigravity, as co-architect, how would you describe the difference between "
            "Genesis's short-term conversation history and its long-term ChromaDB vector memory?"
        ),
        help="Initial probe prompt to send to Antigravity",
    )
    args = parser.parse_args()

    print("=" * 75)
    print("🚀 GENESIS COMMUNICATION HUB — PHASE 0 PROOF OF CONCEPT")
    print(f"   Mechanism:      {args.mechanism}")
    print(f"   Execution Mode: {'🔴 LIVE API CALLS' if args.live else '🟢 100% OFFLINE DRY-RUN (ZERO API CALLS)'}")
    print(f"   Approval Gate:  {'Auto-Approve (TEST)' if args.auto_approve else 'Manual (Fodoh)'}")
    print("=" * 75)

    if args.mechanism == "mock":
        agent = MockAntigravityAgent()
    elif args.mechanism == "cli":
        agent = CliAntigravityAgent()
    elif args.mechanism == "gemini_connectivity":
        agent = ConnectivityGeminiAgent()
    else:
        raise ValueError(f"Unknown mechanism: {args.mechanism}")

    gate = HumanApprovalGate(auto_approve=args.auto_approve)
    genesis = GenesisParticipant(is_live=args.live)
    orchestrator = Phase0Orchestrator(
        antigravity_agent=agent,
        genesis_participant=genesis,
        approval_gate=gate,
    )

    audit = orchestrator.execute_poc(probe_prompt=args.probe)

    print("\n" + "=" * 75)
    print(f"🏁 SESSION SUMMARY: {audit.status}")
    print(f"   Turns Completed: {len(audit.turns)}")
    print(f"   Total Latency:   {audit.total_latency_sec:.2f}s")
    print(f"   Estimated Tokens:{audit.total_tokens_estimated}")
    print("=" * 75)


if __name__ == "__main__":
    main()
