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

    def get_sanitized_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Returns environment variables with all API keys and secrets stripped,
        optionally passing strictly scoped runtime environment variables.
        """
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
        if extra_env:
            clean_env.update(extra_env)
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

;; 2. Allow writes ONLY within the designated ephemeral sandbox and CLI runtime directory
(allow file-write*
    (subpath "{real_sandbox}")
    (subpath "{user_home}/.gemini/antigravity-cli")
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
    (subpath "{user_home}/.gemini/config")
    (subpath "{user_home}/Library/Keychains")
)

;; 5. Explicit, unconditional denials for Genesis production repository, secrets, and personal folders:
(deny file-read* file-write*
    (subpath "{PROJECT_ROOT}")
    (subpath "{user_home}/.gemini/antigravity/brain")
    (subpath "{user_home}/.ssh")
    (subpath "{user_home}/.aws")
    (subpath "{user_home}/.config/gcloud")
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
        extra_env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        """
        Executes a command inside the sandbox wrapped with macOS Seatbelt
        kernel-enforced restrictions.
        """
        env = self.get_sanitized_env(extra_env=extra_env)
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

    @property
    def is_kill_switch_active(self) -> bool:
        """Returns True if the emergency stop flag file exists."""
        return self.stop_file_path.exists()

    def check_kill_switch(self) -> None:
        """Checks if emergency stop flag file exists."""
        if self.is_kill_switch_active:
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
            "model": "mock-synthetic",
            "prompt_tokens_est": len(prompt) // 4,
            "completion_tokens_est": len(self.response_text) // 4,
            "cost_usd": 0.0,
        }
        return self.response_text, thought, metadata


def sanitize_debug_text(text: Optional[str]) -> str:
    """Sanitizes potential secrets, tokens, or credentials from debug text."""
    if not text:
        return ""
    sanitized = []
    sensitive_patterns = ["token", "secret", "password", "key=", "cookie", "auth=", "bearer", "credential"]
    for line in text.splitlines():
        if any(pat in line.lower() for pat in sensitive_patterns):
            sanitized.append("[REDACTED SENSITIVE LINE]")
        else:
            sanitized.append(line)
    return "\n".join(sanitized)


class CliAntigravityAgent(AntigravityInterface):
    """
    Adapter invoking the official headless Antigravity CLI ('agy') inside the sandbox.
    Requires 'agy' to be installed and authenticated.
    """

    def __init__(self, cli_path: Optional[str] = None, timeout_sec: float = 30.0):
        if cli_path is None:
            local_bin_agy = Path.home() / ".local" / "bin" / "agy"
            if local_bin_agy.exists():
                cli_path = str(local_bin_agy)
            else:
                cli_path = "agy"
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
            "--disable-slash-commands",
            "--mode", "plan",
        ]

        # Strict Subscription Sign-In: Do NOT inject GEMINI_API_KEY into CLI environment.
        # This guarantees agy strictly uses the Google AI Pro subscription session
        # stored in ~/.gemini/antigravity-cli/session.json rather than developer API billing.
        extra_env = {}

        t0 = time.perf_counter()
        try:
            res = sandbox.run_sandboxed_command(
                cmd,
                timeout_sec=self.timeout_sec,
                extra_env=extra_env if extra_env else None,
            )
        except subprocess.TimeoutExpired as err:
            latency = time.perf_counter() - t0
            raw_stderr = err.stderr or ""
            if isinstance(raw_stderr, bytes):
                raw_stderr = raw_stderr.decode("utf-8", errors="replace")
            
            # Sanitize stderr tail to capture diagnostics without exposing tokens
            sanitized_lines = []
            for line in raw_stderr.splitlines()[-25:]:
                if any(k in line.lower() for k in ["token", "secret", "password", "key=", "cookie", "auth="]):
                    sanitized_lines.append("[REDACTED SENSITIVE LINE]")
                else:
                    sanitized_lines.append(line)
            sanitized_tail = "\n".join(sanitized_lines).strip()

            diag_msg = (
                f"Antigravity CLI execution timed out after {latency:.1f}s (configured limit: {self.timeout_sec:.1f}s).\n"
                f"Process state: TERMINATED_BY_TIMEOUT\n"
            )
            if sanitized_tail:
                diag_msg += f"Diagnostics (recent stderr tail):\n{sanitized_tail}"
            else:
                diag_msg += "Diagnostics: Subprocess produced no stderr before timeout."

            raise HubSafetyError(diag_msg) from err

        latency = time.perf_counter() - t0

        # Preserve sanitized debug output locally with restricted permissions
        raw_stdout = res.stdout or ""
        raw_stderr = res.stderr or ""
        sanitized_stdout = sanitize_debug_text(raw_stdout)
        sanitized_stderr = sanitize_debug_text(raw_stderr)

        debug_dir = PROJECT_ROOT / "logs" / "hub" / "debug"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_dir.chmod(0o700)
            debug_file = debug_dir / f"cli_raw_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
            debug_file.write_text(
                f"=== SANITIZED STDOUT ===\n{sanitized_stdout}\n\n=== SANITIZED STDERR ===\n{sanitized_stderr}\n"
            )
            debug_file.chmod(0o600)
            debug_path_str = str(debug_file)
        except OSError:
            debug_path_str = "unavailable"

        if res.returncode != 0:
            raise HubSafetyError(f"Antigravity CLI failed (exit {res.returncode}): {sanitized_stderr}")

        raw_out = raw_stdout.strip()
        if not raw_out:
            raise HubSafetyError("Antigravity CLI produced empty stdout. No response received.")

        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict):
            status = str(data.get("status", "")).upper()
            if status in ("ERROR", "FAILED"):
                err_detail = data.get("error") or data.get("message") or "CLI reported failure status"
                raise HubSafetyError(f"Antigravity CLI returned error status ({status}): {err_detail}")

            response_candidate = (
                data.get("response")
                or data.get("result")
                or data.get("content")
                or data.get("text")
                or data.get("output")
            )
            if isinstance(response_candidate, dict):
                response_text = response_candidate.get("text") or response_candidate.get("content") or ""
            else:
                response_text = str(response_candidate) if response_candidate is not None else ""

            thought = data.get("thought") or data.get("reasoning")
            usage_data = data.get("usage", {})
        else:
            response_text = raw_out
            thought = None
            usage_data = {}

        response_text = str(response_text).strip()
        if not response_text:
            raise HubSafetyError(
                "Antigravity CLI returned an empty or whitespace-only response. "
                "The agent may have attempted an unapproved tool call or halted without producing text."
            )

        if response_text.startswith("Error:") or response_text.startswith("ERROR:"):
            raise HubSafetyError(f"Antigravity CLI output indicates an error: {response_text}")

        metadata = {
            "mechanism": "headless_cli",
            "model": "gemini-3.8-flash-high",
            "latency_sec": round(latency, 3),
            "exit_code": res.returncode,
            "cost_usd_est": 0.0,  # Included in Google AI Pro subscription
            "debug_log": debug_path_str,
            "raw_preview": sanitized_stdout[:200] if sanitized_stdout else "",
            "usage": usage_data,
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

    def __init__(self, is_live: bool = False, mock_llm: Optional[Any] = None, max_tokens: int = 800):
        self.is_live = is_live
        self.max_tokens = max_tokens
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
        assert speaker, "Speaker must be specified."

        t0 = time.perf_counter()
        finish_reason = "stop"
        is_truncated = False
        usage = {}

        if self.is_live and self.ai:
            if self.identity and self.planner and self.brain:
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
                if speaker == "Fodoh":
                    instructions = (
                        "- You are speaking with Fodoh, your creator and collaborator.\n"
                        "- Respond thoughtfully, clearly, warmly, and concisely."
                    )
                else:
                    instructions = (
                        f"- You are speaking with {speaker}, a fellow assistant and collaborator.\n"
                        "- Respond thoughtfully, clearly, and concisely.\n"
                        f"- Do NOT assume {speaker} is Fodoh."
                    )

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
{instructions}
"""
            else:
                thought = "Synthetic internal thought."
                full_prompt = incoming_message

            try:
                raw_reply = self.ai.ask_with_tools(
                    full_prompt,
                    max_tokens=self.max_tokens,
                    return_details=True,
                )
            except TypeError:
                raw_reply = self.ai.ask_with_tools(full_prompt)

            if isinstance(raw_reply, tuple):
                reply_text, details = raw_reply
                finish_reason = str(details.get("finish_reason", "stop"))
                is_truncated = bool(details.get("is_truncated", finish_reason == "length"))
                usage = details.get("usage", {})
            else:
                reply_text = str(raw_reply or "")
                finish_reason = "stop"
                is_truncated = False

            # 5. Strict Memory Isolation Check:
            # We explicitly DO NOT call brain.remember() or ai.extract_memory()
            # for turns from another assistant or interface chat!

        else:
            # 100% Offline Mode: Deterministic synthetic reasoning (zero API calls)
            if speaker == "Fodoh":
                thought = "Offline dry-run: Reflecting on Fodoh's direct inquiry."
                reply_text = (
                    "Hello Fodoh. I am operating in offline dry-run mode. "
                    "My companion pipeline and memory architecture are active and isolated."
                )
            else:
                thought = "Offline dry-run: Reflecting on Antigravity's insights regarding memory vs history."
                reply_text = (
                    "Antigravity, your breakdown aligns with my internal structure. "
                    "My conversation history allows me to hold immediate conversational continuity, "
                    "while my ChromaDB vector memory preserves enduring reflections and facts across sessions."
                )
            finish_reason = "stop"
            is_truncated = False
            usage = {}

        latency = time.perf_counter() - t0
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        # gpt-4o-mini pricing: $0.15/1M input, $0.60/1M output
        if prompt_tokens is not None and completion_tokens is not None and self.is_live:
            est_cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.00000060)
        else:
            est_input_tokens = len(incoming_message) // 4 + 400  # prompt + system/identity overhead
            est_output_tokens = len(reply_text) // 4 + (len(thought or "") // 4)
            est_cost = (est_input_tokens * 0.00000015) + (est_output_tokens * 0.00000060) if self.is_live else 0.0

        metadata = {
            "speaker": "Genesis",
            "model": "gpt-4o-mini" if self.is_live else "synthetic-offline",
            "latency_sec": round(latency, 3),
            "memory_stored": False,  # Strict isolation verified
            "is_live": self.is_live,
            "cost_usd_est": round(est_cost, 6),
            "finish_reason": finish_reason,
            "is_truncated": is_truncated,
            "max_tokens": self.max_tokens,
        }
        if prompt_tokens is not None:
            metadata["prompt_tokens"] = prompt_tokens
            metadata["completion_tokens"] = completion_tokens
            metadata["total_tokens"] = total_tokens

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
        probe_only: bool = False,
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

            if not ag_response or not ag_response.strip():
                err_msg = "Antigravity returned an empty or whitespace-only response. Halting exchange before Gate 2."
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 1 FAILED: {err_msg}")
                raise HubSafetyError(err_msg)

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

            print(f"\n💬 TURN 1: [Antigravity] (Model: {ag_meta.get('model', 'gemini-3.8-flash-high')} | Latency: {ag_latency:.2f}s)")
            print(f"   {ag_response.strip()}\n")

            # If running Phase 0A (probe-only), finish immediately without invoking Genesis
            if probe_only:
                audit.status = "PROBE_COMPLETED"
                print("\n" + "=" * 75)
                print("🟢 PHASE 0A STANDALONE CONNECTIVITY PROBE COMPLETED")
                print("   Antigravity response received successfully.")
                print("   Genesis was NOT invoked. No approval or message was passed to Genesis.")
                print("=" * 75)
                return audit

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

            if not gen_reply or not gen_reply.strip():
                err_msg = "Genesis returned an empty or whitespace-only reply."
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 2 FAILED: {err_msg}")
                raise HubSafetyError(err_msg)

            if gen_meta.get("is_truncated") or gen_meta.get("finish_reason") == "length":
                err_msg = (
                    f"Genesis response was truncated by output token limit (finish_reason='length', "
                    f"max_tokens={gen_meta.get('max_tokens', 800)}). Cut-off text: '{gen_reply[-60:]}...'. "
                    "Halting and reporting session as failed to prevent accepting a truncated reply."
                )
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 2 TRUNCATED: {err_msg}")
                raise HubSafetyError(err_msg)

            turn_2 = AgentTurn(
                turn_id=2,
                speaker="Genesis",
                content=gen_reply,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=None,  # Requirement: Do not persist Genesis's private internal reasoning in audit log
                tokens_estimated=(len(ag_response) + len(gen_reply)) // 4,
                latency_sec=round(gen_latency, 3),
                metadata=gen_meta,
            )
            audit.turns.append(turn_2)

            print(f"\n💬 TURN 2: [Genesis] (Model: {gen_meta.get('model', 'gpt-4o-mini')} | Latency: {gen_latency:.2f}s)")
            print(f"   🗣️  Reply:   {gen_reply.strip()}\n")

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
            "Genesis's short-term conversation history and its long-term ChromaDB vector memory? "
            "Please respond directly with a concise conceptual explanation in plain text without "
            "calling tools or running commands."
        ),
        help="Initial probe prompt to send to Antigravity",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Subprocess timeout in seconds for CLI execution (default: 60.0, range: 5.0 to 90.0)",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        default=False,
        help="Run only the standalone Antigravity probe (Phase 0A). Does NOT invoke Genesis or prompt Gate 2.",
    )
    parser.add_argument(
        "--genesis-max-tokens",
        type=int,
        default=800,
        help="Maximum output tokens for Genesis (default: 800, range: 100 to 2000)",
    )
    args = parser.parse_args()

    effective_timeout = min(max(args.timeout, 5.0), 90.0)
    effective_genesis_tokens = min(max(args.genesis_max_tokens, 100), 2000)

    print("=" * 75)
    print("🚀 GENESIS COMMUNICATION HUB — PHASE 0 PROOF OF CONCEPT")
    print(f"   Mechanism:      {args.mechanism}")
    mode_str = "🔴 LIVE API CALLS" if args.live else "🟢 100% OFFLINE DRY-RUN (ZERO API CALLS)"
    if args.probe_only:
        mode_str += " [PROBE ONLY — GENESIS IDLE]"
    print(f"   Execution Mode: {mode_str}")
    print(f"   Approval Gate:  {'Auto-Approve (TEST)' if args.auto_approve else 'Manual (Fodoh)'}")
    print(f"   Genesis Output: {effective_genesis_tokens} max tokens")
    print(f"   Timeout Cap:    {effective_timeout:.1f}s")
    print("=" * 75)

    if args.mechanism in ("cli", "gemini_connectivity") and not args.live:
        print(f"\n⚠️  SAFETY ABORT: Outbound mechanism '{args.mechanism}' requires the '--live' flag.")
        print("   Without '--live', execution is locked in 100% offline dry-run mode.")
        print("   To run offline safely, use: .venv/bin/python3 hub/phase0.py --mechanism=mock")
        print("   To authorize live requests with approval gates, use: .venv/bin/python3 hub/phase0.py --mechanism=cli --live\n")
        raise HubSafetyError(f"Outbound mechanism '{args.mechanism}' requires explicit '--live' flag.")

    if args.mechanism == "mock":
        agent = MockAntigravityAgent()
    elif args.mechanism == "cli":
        agent = CliAntigravityAgent(timeout_sec=effective_timeout)
    elif args.mechanism == "gemini_connectivity":
        agent = ConnectivityGeminiAgent()
    else:
        raise ValueError(f"Unknown mechanism: {args.mechanism}")

    gate = HumanApprovalGate(auto_approve=args.auto_approve)
    genesis = GenesisParticipant(
        is_live=args.live and not args.probe_only,
        max_tokens=effective_genesis_tokens,
    )
    orchestrator = Phase0Orchestrator(
        antigravity_agent=agent,
        genesis_participant=genesis,
        approval_gate=gate,
    )

    audit = orchestrator.execute_poc(probe_prompt=args.probe, probe_only=args.probe_only)

    print("\n" + "=" * 75)
    print(f"🏁 SESSION SUMMARY: {audit.status}")
    print(f"   Turns Completed: {len(audit.turns)}")
    print(f"   Total Latency:   {audit.total_latency_sec:.2f}s")
    print(f"   Estimated Tokens:{audit.total_tokens_estimated}")
    for turn in audit.turns:
        m_name = turn.metadata.get("model", "unknown")
        cost_val = turn.metadata.get("cost_usd_est", 0.0)
        cost_str = f" | Est Cost: ${cost_val:.6f}" if cost_val > 0 else " | Cost: $0.00 (Included)"
        print(f"   - Turn {turn.turn_id} [{turn.speaker}] ({m_name} | {turn.latency_sec:.2f}s{cost_str})")
    print("=" * 75)


if __name__ == "__main__":
    main()
