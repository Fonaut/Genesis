#!/usr/bin/env python3
"""
Genesis Communication Hub — Phase 1: Three-Agent Collaborative Hub
===================================================================

Enables supervised, human-gated multi-agent collaboration among:
1. Antigravity (CliAntigravityAgent via OS Seatbelt Sandbox)
2. OpenAI-Agent (Independent OpenAI API-powered research & analysis agent)
3. Genesis (GenesisParticipant: Identity, Planner, Vector Recall, Response Generation)

CRITICAL SAFETY & GOVERNANCE INVARIANTS:
1. 100% Offline Default: Live calls require explicit '--live' flag and interactive approval.
2. Distinct Agent Identities: OpenAI-Agent is explicitly labeled as an independent API agent,
   with no automatic link or access to Fodoh's ChatGPT app conversations or account memory.
3. Human-in-the-Loop Gates: Every message transmission requires Fodoh's explicit approval.
4. Hard Governance Ceilings: Turn count hard cap (default: 3) and spending limit cap (default: $0.05).
5. Strict Memory Boundary: Assistant-to-assistant dialogue is NEVER stored in Genesis's personal
   long-term memory (memories.json or ChromaDB).
6. Tool Suppression & Sandboxing: Zero unauthorized commands, file edits, or credential access.
   OpenAI-Agent operates strictly with no tools (tools=None). Antigravity CLI runs under macOS Seatbelt.
7. Privacy & Sanitization: Genesis's internal reasoning (Thought) is excluded from persistent audit logs.
   All raw CLI debug logs are sanitized of tokens and stored with 0o600 permissions.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hub.phase0 import (
    AgentTurn,
    AntigravityInterface,
    CliAntigravityAgent,
    ExecutionCancelledError,
    GenesisParticipant,
    HumanApprovalGate,
    HubSafetyError,
    KillSwitchActiveError,
    MockAntigravityAgent,
    SandboxManager,
    SessionAudit,
    TurnLimitReachedError,
    sanitize_debug_text,
)


class CostLimitExceededError(HubSafetyError):
    """Raised when estimated cumulative API spending exceeds the hard budget limit."""
    pass


# ---------------------------------------------------------------------------
# Phase 1 Safety Governor with Spending Limits
# ---------------------------------------------------------------------------

class SafetyGovernor:
    """Enforces execution ceilings, timeouts, cost caps, and stop controls."""

    def __init__(
        self,
        max_turns: int = 3,
        stop_file: str = ".hub_stopped",
        max_cost_usd: float = 0.05,
    ):
        self.max_turns = max_turns
        self.stop_file_path = PROJECT_ROOT / stop_file
        self.max_cost_usd = max_cost_usd
        self.current_turns = 0
        self.cumulative_cost_usd = 0.0

    def check_kill_switch(self) -> None:
        """Checks if emergency stop flag file exists."""
        if self.stop_file_path.exists():
            raise KillSwitchActiveError(
                f"Emergency stop file detected at: {self.stop_file_path}. Execution aborted immediately."
            )

    def record_turn(self, estimated_cost_usd: float = 0.0) -> None:
        """Tracks turn count and trips if hard turn or cost ceiling is exceeded."""
        self.check_kill_switch()
        self.current_turns += 1
        if self.current_turns > self.max_turns:
            raise TurnLimitReachedError(
                f"Turn limit exceeded: attempted turn {self.current_turns} with hard cap of {self.max_turns}."
            )
        self.cumulative_cost_usd += estimated_cost_usd
        if self.cumulative_cost_usd > self.max_cost_usd:
            raise CostLimitExceededError(
                f"Cost cap exceeded: session accumulated ${self.cumulative_cost_usd:.5f} USD "
                f"(hard ceiling: ${self.max_cost_usd:.5f} USD). Execution halted immediately."
            )


# ---------------------------------------------------------------------------
# Independent OpenAI-Powered Agent Participant
# ---------------------------------------------------------------------------

class OpenAIAgentParticipant:
    """
    Independent AI collaborator powered directly by the OpenAI API.

    CRITICAL BOUNDARIES:
    - Identified clearly as 'OpenAI-Agent' (an independent API agent).
    - Has its own distinct system instructions focused on research, strategy, and architecture review.
    - Disclaims any automated access to Fodoh's ChatGPT app conversations or personal account memory.
    - Has NO tool access (tools=None, tool_choice=None), guaranteeing zero command or filesystem execution.
    - Strictly isolated from Genesis's personal ChromaDB vector memory.
    """

    def __init__(
        self,
        is_live: bool = False,
        model: str = "gpt-4o-mini",
        mock_response: Optional[str] = None,
        timeout_sec: float = 30.0,
        max_tokens: int = 800,
        mock_finish_reason: str = "stop",
    ):
        self.is_live = is_live
        self.model = model
        self.mock_response = mock_response or (
            "From an OpenAI research perspective, a three-agent division of labor offers clear strengths: "
            "Antigravity provides deep technical systems engineering; Genesis acts as the primary voice "
            "companion and contextual coordinator; while this OpenAI agent contributes objective analysis, "
            "business strategy critique, and discovery synthesis. This division prevents duplicate effort "
            "while keeping Fodoh in full control as the central decision-maker."
        )
        self.timeout_sec = timeout_sec
        self.max_tokens = max_tokens
        self.mock_finish_reason = mock_finish_reason

        self.system_prompt = (
            "You are an independent AI collaborator powered by the OpenAI API, participating in a "
            "supervised multi-agent discussion with Genesis (an AI companion) and Antigravity (a technical co-architect).\n"
            "Your role: Provide objective analysis, technical critique, strategic planning, and research support.\n"
            "Key constraints:\n"
            "- You are an independent agent using OpenAI API models. You DO NOT have access to Fodoh's private ChatGPT app history or personal account memories.\n"
            "- You are conversing with fellow AI assistants and Fodoh (the human orchestrator).\n"
            "- Be concise, direct, constructive, and analytical.\n"
            "- Do NOT attempt to run tools, execute commands, or access files; you operate purely as an analytical text reasoning engine.\n"
        )

    def process_turn(
        self,
        speaker: str,
        incoming_message: str,
        formatted_history: str = "(No previous conversation)",
        chatgpt_summary_context: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Executes the OpenAI API agent's reasoning turn.
        Returns (reply_text, metadata).
        """
        t0 = time.perf_counter()

        if self.is_live:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise HubSafetyError("OPENAI_API_KEY is not configured in environment or .env file.")

            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            full_system = self.system_prompt
            if chatgpt_summary_context and chatgpt_summary_context.strip():
                full_system += (
                    "\n\nReference Discussion Briefing (provided explicitly by Fodoh):\n"
                    f"{chatgpt_summary_context.strip()}\n"
                    "Use this briefing for context when formulating your analysis."
                )

            user_payload = (
                f"Previous conversation context:\n{formatted_history}\n\n"
                f"[{speaker}]:\n{incoming_message}\n\n"
                "Please provide your objective analysis, critique, and perspective."
            )

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": user_payload},
                ],
                max_tokens=self.max_tokens,
                temperature=0.7,
                timeout=self.timeout_sec,
                # Explicitly NO tools passed
            )

            latency = time.perf_counter() - t0
            choice = response.choices[0]
            reply_text = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
            is_truncated = (finish_reason == "length")

            prompt_tokens = (
                response.usage.prompt_tokens
                if response.usage
                else (len(full_system) + len(user_payload)) // 4
            )
            completion_tokens = (
                response.usage.completion_tokens
                if response.usage
                else len(reply_text) // 4
            )
            total_tokens = (
                response.usage.total_tokens
                if response.usage
                else prompt_tokens + completion_tokens
            )
        else:
            # 100% Offline Mode: Deterministic synthetic reasoning (zero API calls)
            time.sleep(0.01)
            latency = time.perf_counter() - t0
            reply_text = self.mock_response
            finish_reason = self.mock_finish_reason
            is_truncated = (finish_reason == "length")
            prompt_tokens = (len(self.system_prompt) + len(incoming_message)) // 4
            completion_tokens = len(reply_text) // 4
            total_tokens = prompt_tokens + completion_tokens

        reply_text = reply_text.strip()
        if not reply_text:
            raise HubSafetyError("OpenAI-Agent returned an empty or whitespace-only response. Exchange halted.")

        # gpt-4o-mini pricing: $0.15/1M input, $0.60/1M output
        est_cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.00000060) if self.is_live else 0.0

        metadata = {
            "speaker": "OpenAI-Agent",
            "model": self.model if self.is_live else "synthetic-offline",
            "latency_sec": round(latency, 3),
            "cost_usd_est": round(est_cost, 6),
            "prompt_tokens_est": prompt_tokens,
            "completion_tokens_est": completion_tokens,
            "total_tokens_est": total_tokens,
            "finish_reason": finish_reason,
            "is_truncated": is_truncated,
            "is_live": self.is_live,
        }

        # Truncation Guard: Prevent cut-off messages from reaching Gate 3 or Genesis
        if is_truncated:
            raise HubSafetyError(
                f"OpenAI-Agent response was truncated by output token limit "
                f"(finish_reason='length', max_tokens={self.max_tokens}). "
                f"Cut-off text: '{reply_text[-60:]}...'. Halting before Gate 3 "
                "to prevent forwarding incomplete text to Genesis."
            )

        return reply_text, metadata


# ---------------------------------------------------------------------------
# Phase 1 Orchestrator: Supervised Multi-Agent Collaboration
# ---------------------------------------------------------------------------

class Phase1Orchestrator:
    """
    Coordinates supervised multi-agent exchanges among:
    1. Antigravity (CliAntigravityAgent or MockAntigravityAgent)
    2. OpenAI-Agent (OpenAIAgentParticipant)
    3. Genesis (GenesisParticipant)
    """

    def __init__(
        self,
        antigravity_agent: AntigravityInterface,
        openai_agent: OpenAIAgentParticipant,
        genesis_participant: Optional[GenesisParticipant] = None,
        approval_gate: Optional[HumanApprovalGate] = None,
        governor: Optional[SafetyGovernor] = None,
        log_dir: Optional[Path] = None,
    ):
        self.antigravity = antigravity_agent
        self.openai_agent = openai_agent
        self.genesis = genesis_participant or GenesisParticipant()
        self.gate = approval_gate or HumanApprovalGate(auto_approve=False)
        self.governor = governor or SafetyGovernor(max_turns=3, max_cost_usd=0.05)
        self.log_dir = log_dir or (PROJECT_ROOT / "logs" / "hub")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = SandboxManager()

    def execute_triad_poc(
        self,
        probe_prompt: str,
        chatgpt_summary_context: Optional[str] = None,
    ) -> SessionAudit:
        """
        Executes a 3-agent supervised sequence:
        Turn 1: Antigravity (CLI via Seatbelt sandbox) responds to probe.
        Gate 2: Fodoh approves passing Antigravity's response to OpenAI-Agent.
        Turn 2: OpenAI-Agent provides independent critique & analysis.
        Gate 3: Fodoh approves passing dialogue synthesis to Genesis.
        Turn 3: Genesis provides companion synthesis & coordination view.
        """
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        is_live_flag = getattr(self.genesis, "is_live", False)
        audit = SessionAudit(
            session_id=session_id,
            start_time=datetime.now(timezone.utc).isoformat(),
            mechanism="Phase1-Triad-Supervised",
            is_dry_run=not is_live_flag,
        )

        t_session_start = time.perf_counter()

        try:
            # -----------------------------------------------------------------
            # Step 1: Human Approval to Probe Antigravity (Gate 1)
            # -----------------------------------------------------------------
            self.gate.request_approval(
                action_name="Send Initial Probe to Antigravity",
                payload_preview=probe_prompt,
                recipient="Antigravity",
            )

            # -----------------------------------------------------------------
            # Step 2: Execute Antigravity Turn (Turn 1)
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

            ag_cost = ag_meta.get("cost_usd_est", 0.0)
            self.governor.cumulative_cost_usd += ag_cost

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

            # -----------------------------------------------------------------
            # Step 3: Human Approval to Pass Antigravity Output to OpenAI-Agent (Gate 2)
            # -----------------------------------------------------------------
            formatted_ag_msg = f"[Antigravity]:\n{ag_response}"
            self.gate.request_approval(
                action_name="Pass Antigravity Response to OpenAI-Agent",
                payload_preview=formatted_ag_msg,
                recipient="OpenAI-Agent",
            )

            # -----------------------------------------------------------------
            # Step 4: Execute OpenAI-Agent Turn (Turn 2)
            # -----------------------------------------------------------------
            self.governor.record_turn()  # Turn 2
            t_openai_start = time.perf_counter()
            openai_reply, openai_meta = self.openai_agent.process_turn(
                speaker="Antigravity",
                incoming_message=ag_response,
                formatted_history=f"[User Probe]: {probe_prompt}",
                chatgpt_summary_context=chatgpt_summary_context,
            )
            openai_latency = time.perf_counter() - t_openai_start

            if not openai_reply or not openai_reply.strip():
                err_msg = "OpenAI-Agent returned an empty or whitespace-only reply. Halting before Gate 3."
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 2 FAILED: {err_msg}")
                raise HubSafetyError(err_msg)

            if openai_meta.get("is_truncated") or openai_meta.get("finish_reason") == "length":
                err_msg = (
                    f"OpenAI-Agent response was truncated by output token limit (finish_reason='length'). "
                    f"Cut-off point: '{openai_reply[-60:]}...'. Halting before Gate 3 to prevent "
                    "forwarding incomplete text to Genesis."
                )
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 2 TRUNCATED: {err_msg}")
                raise HubSafetyError(err_msg)

            openai_cost = openai_meta.get("cost_usd_est", 0.0)
            self.governor.cumulative_cost_usd += openai_cost
            if self.governor.cumulative_cost_usd > self.governor.max_cost_usd:
                raise CostLimitExceededError(
                    f"Cost cap exceeded after Turn 2: accumulated ${self.governor.cumulative_cost_usd:.5f} USD "
                    f"(hard limit: ${self.governor.max_cost_usd:.5f} USD)."
                )

            turn_2 = AgentTurn(
                turn_id=2,
                speaker="OpenAI-Agent",
                content=openai_reply,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=None,
                tokens_estimated=(len(ag_response) + len(openai_reply)) // 4,
                latency_sec=round(openai_latency, 3),
                metadata=openai_meta,
            )
            audit.turns.append(turn_2)

            cost_str = f" | Est Cost: ${openai_cost:.6f}" if openai_cost > 0 else ""
            print(f"\n💬 TURN 2: [OpenAI-Agent] (Model: {openai_meta.get('model', 'gpt-4o-mini')} | Latency: {openai_latency:.2f}s{cost_str})")
            print(f"   {openai_reply.strip()}\n")

            # -----------------------------------------------------------------
            # Step 5: Human Approval to Pass Multi-Agent Context to Genesis (Gate 3)
            # -----------------------------------------------------------------
            multi_agent_payload = (
                f"[Antigravity]:\n{ag_response}\n\n"
                f"[OpenAI-Agent]:\n{openai_reply}"
            )
            self.gate.request_approval(
                action_name="Pass Multi-Agent Dialogue to Genesis",
                payload_preview=multi_agent_payload,
                recipient="Genesis",
            )

            # -----------------------------------------------------------------
            # Step 6: Execute Genesis Response Pipeline (Turn 3)
            # -----------------------------------------------------------------
            self.governor.record_turn()  # Turn 3
            t_gen_start = time.perf_counter()
            gen_reply, gen_thought, gen_meta = self.genesis.process_turn(
                speaker="OpenAI-Agent",
                incoming_message=(
                    f"Antigravity and OpenAI-Agent have shared their perspectives:\n\n{multi_agent_payload}\n\n"
                    "How do you synthesize these perspectives from your role as AI companion and coordinator?"
                ),
                formatted_history=f"[User Probe]: {probe_prompt}",
            )
            gen_latency = time.perf_counter() - t_gen_start

            if not gen_reply or not gen_reply.strip():
                err_msg = "Genesis returned an empty or whitespace-only reply."
                audit.status = "FAILED"
                audit.error_message = err_msg
                print(f"\n🛑 TURN 3 FAILED: {err_msg}")
                raise HubSafetyError(err_msg)

            gen_cost = gen_meta.get("cost_usd_est", 0.0)
            self.governor.cumulative_cost_usd += gen_cost
            if self.governor.cumulative_cost_usd > self.governor.max_cost_usd:
                raise CostLimitExceededError(
                    f"Cost cap exceeded after Turn 3: accumulated ${self.governor.cumulative_cost_usd:.5f} USD."
                )

            turn_3 = AgentTurn(
                turn_id=3,
                speaker="Genesis",
                content=gen_reply,
                timestamp=datetime.now(timezone.utc).isoformat(),
                thought=None,  # Suppressed from audit log
                tokens_estimated=(len(multi_agent_payload) + len(gen_reply)) // 4,
                latency_sec=round(gen_latency, 3),
                metadata=gen_meta,
            )
            audit.turns.append(turn_3)

            cost_str = f" | Est Cost: ${gen_cost:.6f}" if gen_cost > 0 else ""
            print(f"\n💬 TURN 3: [Genesis] (Model: {gen_meta.get('model', 'gpt-4o-mini')} | Latency: {gen_latency:.2f}s{cost_str})")
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
        """Saves a structured JSON log of the Phase 1 session."""
        log_file = self.log_dir / f"phase1_{audit.session_id}.json"
        log_file.write_text(json.dumps(asdict(audit), indent=2))
        print(f"\n📄 Audit record persisted to: {log_file}")
        return log_file


# ---------------------------------------------------------------------------
# CLI Command Entry Point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Communication Hub — Phase 1 Triad Collaboration")
    parser.add_argument(
        "--mechanism",
        choices=["mock", "cli"],
        default="mock",
        help="Antigravity integration mechanism (default: mock for safe offline verification)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Bypass interactive approval gates (for automated tests only)",
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
            "Antigravity, as co-architect, how should our three-agent collaboration system "
            "(Genesis, Antigravity, and OpenAI-Agent) divide technical, companion, and analytical "
            "responsibilities to help Fodoh achieve his goals without duplicate effort? "
            "Please respond directly with a concise conceptual answer in plain text without tools."
        ),
        help="Initial probe prompt to dispatch to Antigravity",
    )
    parser.add_argument(
        "--chatgpt-summary-file",
        type=str,
        default=None,
        help="Path to an approved text file containing Fodoh's selected summary of relevant ChatGPT discussions",
    )
    parser.add_argument(
        "--chatgpt-summary",
        type=str,
        default=None,
        help="Inline approved summary text from Fodoh's ChatGPT discussions to provide as background",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=0.05,
        help="Hard ceiling on cumulative session API cost in USD (default: $0.05)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Subprocess timeout in seconds for CLI execution (default: 60.0)",
    )
    parser.add_argument(
        "--openai-max-tokens",
        type=int,
        default=800,
        help="Maximum output tokens for OpenAI-Agent (default: 800, range: 100 to 2000)",
    )
    args = parser.parse_args()

    effective_timeout = min(max(args.timeout, 5.0), 90.0)
    effective_openai_tokens = min(max(args.openai_max_tokens, 100), 2000)

    # Ingestion of user-approved ChatGPT summary briefing
    briefing_context = args.chatgpt_summary
    if args.chatgpt_summary_file:
        summary_path = Path(args.chatgpt_summary_file).resolve()
        if not summary_path.exists():
            raise FileNotFoundError(f"Specified ChatGPT summary file does not exist: {summary_path}")
        file_content = summary_path.read_text(encoding="utf-8")
        briefing_context = (briefing_context + "\n" + file_content) if briefing_context else file_content

    print("=" * 75)
    print("🚀 GENESIS COMMUNICATION HUB — PHASE 1: THREE-AGENT COLLABORATION")
    print(f"   Participants:   Antigravity, OpenAI-Agent, Genesis")
    print(f"   Mechanism:      {args.mechanism}")
    mode_str = "🔴 LIVE API CALLS" if args.live else "🟢 100% OFFLINE DRY-RUN (ZERO API CALLS)"
    print(f"   Execution Mode: {mode_str}")
    print(f"   Approval Gate:  {'Auto-Approve (TEST)' if args.auto_approve else 'Manual (Fodoh)'}")
    print(f"   OpenAI Output:  {effective_openai_tokens} max tokens")
    print(f"   Cost Ceiling:   ${args.max_cost:.4f} USD")
    print(f"   Timeout Cap:    {effective_timeout:.1f}s")
    if briefing_context:
        print(f"   ChatGPT Context:Active ({len(briefing_context)} chars)")
    print("=" * 75)

    if args.mechanism == "cli" and not args.live:
        print("\n⚠️  SAFETY ABORT: Outbound mechanism 'cli' requires the '--live' flag.")
        print("   Without '--live', execution is locked in 100% offline dry-run mode.")
        print("   To run offline safely, use: .venv/bin/python3 hub/phase1.py --mechanism=mock")
        print("   To authorize live requests with approval gates, use: .venv/bin/python3 hub/phase1.py --mechanism=cli --live\n")
        raise HubSafetyError("Outbound mechanism 'cli' requires explicit '--live' flag.")

    if args.mechanism == "mock":
        ag_agent = MockAntigravityAgent()
    elif args.mechanism == "cli":
        ag_agent = CliAntigravityAgent(timeout_sec=effective_timeout)
    else:
        raise ValueError(f"Unknown mechanism: {args.mechanism}")

    openai_agent = OpenAIAgentParticipant(is_live=args.live, max_tokens=effective_openai_tokens)
    genesis = GenesisParticipant(is_live=args.live)
    gate = HumanApprovalGate(auto_approve=args.auto_approve)
    governor = SafetyGovernor(max_turns=3, max_cost_usd=args.max_cost)

    orchestrator = Phase1Orchestrator(
        antigravity_agent=ag_agent,
        openai_agent=openai_agent,
        genesis_participant=genesis,
        approval_gate=gate,
        governor=governor,
    )

    audit = orchestrator.execute_triad_poc(
        probe_prompt=args.probe,
        chatgpt_summary_context=briefing_context,
    )

    total_session_cost = sum(t.metadata.get("cost_usd_est", 0.0) for t in audit.turns)

    print("\n" + "=" * 75)
    print(f"🏁 SESSION SUMMARY: {audit.status}")
    print(f"   Turns Completed: {len(audit.turns)}")
    print(f"   Total Latency:   {audit.total_latency_sec:.2f}s")
    print(f"   Estimated Tokens:{audit.total_tokens_estimated}")
    print(f"   Total Est Cost:  ${total_session_cost:.6f} USD")
    for turn in audit.turns:
        m_name = turn.metadata.get("model", "unknown")
        cost_val = turn.metadata.get("cost_usd_est", 0.0)
        cost_str = f" | Est Cost: ${cost_val:.6f}" if cost_val > 0 else " | Cost: $0.00 (Included)"
        print(f"   - Turn {turn.turn_id} [{turn.speaker}] ({m_name} | {turn.latency_sec:.2f}s{cost_str})")
    print("=" * 75)


if __name__ == "__main__":
    main()
