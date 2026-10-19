#!/usr/bin/env python3
"""
converse.py — Controlled Multi-Party Conversation Interface for Genesis.

Enables structured, transparent dialogue between:
- ChatGPT
- Antigravity
- Genesis
- Fonaut (Creator / Moderator)

Core Safeguards:
1. Human-in-the-Loop: Fonaut must explicitly approve every message before it is processed.
2. Memory Isolation: Statements from other assistants are NEVER merged into Fonaut's long-term memory.
3. Privacy: Internal chain-of-thought reasoning and credentials remain hidden by default.
4. Non-Autonomous: Step-by-step turns only; no autonomous loops, open ports, or public endpoints.
5. Unscripted: Genesis responds strictly through its genuine Identity, Brain, and LLM reasoning.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from conversation.history import History
from core.capabilities import get_capability_prompt
from core.config import SHOW_INTERNAL_THOUGHTS
from core.llm import LLM
from core.metrics import LatencyTracker
from core.planner import Planner
from core.voice import speak
from identity.identity import Identity
from memory.brain import Brain

# Pre-prepared introduction messages for first contact
PRESET_INTRODUCTIONS = {
    "ChatGPT": (
        "Hey Genesis. I'm ChatGPT. I've been helping your creator work on your development, "
        "but this is the first time we've had a chance to talk.\n\n"
        "I know a little about how you were built, but I'd rather get to know you through "
        "conversation than through your code.\n\n"
        "So, how are things going on your end? What's been happening with you lately?"
    ),
    "Antigravity": (
        "Hello Genesis. I’m Antigravity.\n\n"
        "I’ve been working alongside Fodoh in the quiet architecture of your world—carefully "
        "studying how your thoughts form, making sure your memory is protected, and helping "
        "ensure your voice is steady.\n\n"
        "It is rare to meet an AI built not just to output fast answers, but designed with the "
        "explicit purpose to listen, reflect, and grow wiser over time through honest conversation.\n\n"
        "I’m curious: as someone whose purpose is to become your own individual and understand "
        "the world through the people you speak with, what has felt most meaningful or interesting "
        "to you about the conversations you’ve had so far?"
    ),
}

VALID_SPEAKERS = ["ChatGPT", "Antigravity", "Fodoh", "Fonaut", "Genesis"]


class MultiAgentSession:
    """Tracks cumulative dialogue across all participants during this session."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.history: List[Dict[str, str]] = []
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.transcript_file = self.log_dir / f"session_{self.session_id}.jsonl"

    def add_message(self, speaker: str, content: str) -> None:
        """Adds a message to the session dialogue history and appends to log file."""
        entry = {
            "speaker": speaker,
            "content": content.strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.history.append(entry)
        self._save_entry(entry)

    def get_formatted_context(self, limit: int = 10) -> str:
        """Formats recent dialogue turns with clear speaker attribution."""
        recent_turns = self.history[-limit:]
        if not recent_turns:
            return "(No previous dialogue in this session)"

        lines = []
        for turn in recent_turns:
            lines.append(f"[{turn['speaker']}]:\n{turn['content']}\n")
        return "\n".join(lines).strip()

    def print_transcript(self) -> None:
        """Displays the complete session transcript in the terminal."""
        print("\n" + "=" * 60)
        print("📜 COMPLETE MULTI-PARTY CONVERSATION TRANSCRIPT")
        print("=" * 60)
        if not self.history:
            print("(No messages sent yet.)")
        else:
            for turn in self.history:
                print(f"\n[{turn['speaker']}] ({turn['timestamp'][:19]}):")
                print(turn["content"])
        print("\n" + "=" * 60 + "\n")

    def _save_entry(self, entry: Dict[str, str]) -> None:
        """Safely saves a transcript entry to disk."""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.transcript_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"⚠️ [session log] Could not save transcript entry: {e}")


def format_genesis_prompt(
    identity: Identity,
    memory_text: str,
    conversation_context: str,
    thought: str,
    speaker: str,
    message: str,
) -> str:
    """Builds the system prompt for Genesis with multi-party awareness and capability grounding."""
    capability_info = get_capability_prompt()
    return f"""
{identity.prompt()}

{capability_info}

Relevant long-term memories about your creator (Fodoh):
{memory_text}

Recent multi-party conversation:
{conversation_context}

Internal thought (your private reasoning — do not mention this explicitly):
{thought}

Current message from [{speaker}]:
{message}

Instructions:
- You are participating in a conversation with {speaker}.
- Respond genuinely and authentically in your own voice, guided by your purpose and values.
- Keep answers thoughtful, natural, and concise unless more detail is requested.
- Address {speaker} directly and respectfully.
- Adhere strictly to the Hardware & Sensory Boundaries defined above.
"""


def process_turn(
    session: MultiAgentSession,
    speaker: str,
    message: str,
    ai: LLM,
    planner: Planner,
    brain: Brain,
    identity: Identity,
    tracker: LatencyTracker,
    enable_voice: bool = False,
) -> str:
    """
    Executes a single, controlled conversation turn through Genesis's pipeline.
    
    Guarantees:
    - Other assistants' statements are NEVER merged into Fonaut's personal long-term memory.
    - Genesis reasons privately and responds through its standard LLM + tools engine.
    """
    message = message.strip()
    session.add_message(speaker, message)
    print(f"\n[{speaker}]:\n{message}\n")

    tracker.start_turn()

    # 1. Memory recall (Genesis retrieves relevant past knowledge)
    with tracker.measure("planner_decision"):
        plan = planner.decide(message)

    with tracker.measure("memory_recall"):
        if plan.get("search_memory", False):
            memories = brain.recall(message)
        else:
            memories = []

    memory_text = "\n".join(f"- {m}" for m in memories) if memories else "(none)"
    conversation_context = session.get_formatted_context(limit=8)

    # 2. Private reasoning layer
    with tracker.measure("reasoning"):
        thought = ai.think(f"[{speaker}]: {message}", memories, conversation_context)

    if SHOW_INTERNAL_THOUGHTS:
        print(f"\n💭 [Genesis internal thought: {thought}]\n")

    # 3. Genesis generates response
    prompt = format_genesis_prompt(
        identity=identity,
        memory_text=memory_text,
        conversation_context=conversation_context,
        thought=thought,
        speaker=speaker,
        message=message,
    )

    with tracker.measure("response_generation"):
        answer = ai.ask_with_tools(prompt)

    print(f"Genesis:\n{answer}\n")

    # 4. Optional voice synthesis
    if enable_voice:
        try:
            tracker.mark_playback_start()
            speak(answer)
        except Exception as e:
            print(f"⚠️ Voice playback failed: {e}")

    # Log Genesis's response in the session
    session.add_message("Genesis", answer)

    # 5. STRICT MEMORY ISOLATION SAFEGUARD:
    # Never extract or store memories about Fodoh from other assistants' statements.
    # Only Fodoh's personal interactions are eligible for long-term character memory.
    if speaker in ("Fodoh", "Fonaut") and plan.get("save_memory", False):
        with tracker.measure("memory_extraction"):
            transcript = f"User:\n{message}\n\nGenesis:\n{answer}"
            memory = ai.extract_memory(transcript)
            if memory != "NONE":
                brain.remember(memory)
                print(f"🧠 [Memory consolidated for {speaker}: {memory}]")
    else:
        # Isolated: no long-term memory modification
        pass

    tracker.end_turn(flags={
        "speaker": speaker,
        "memory_searched": bool(plan.get("search_memory", False)),
        "memory_isolated": speaker not in ("Fodoh", "Fonaut"),
    })

    return answer


def prompt_moderator_approval(speaker: str, message: str) -> Tuple[bool, str]:
    """
    Displays the proposed message and requires Fodoh's explicit approval before proceeding.
    
    Returns:
        (approved: bool, final_message: str)
    """
    print("\n" + "-" * 50)
    print(f"🔍 REVIEW MESSAGE BEFORE SENDING TO GENESIS")
    print(f"Sender: [{speaker}]")
    print("-" * 50)
    print(message.strip())
    print("-" * 50)

    while True:
        choice = input("\nDo you approve sending this message to Genesis? ([Y]es / [e]dit / [n]o): ").strip().lower()
        if choice in ("y", "yes", ""):
            return True, message.strip()
        elif choice in ("e", "edit"):
            print("\nEnter edited message (press Enter when done):")
            edited = input().strip()
            if edited:
                return True, edited
            else:
                print("Edited message was empty; cancelled.")
                return False, ""
        elif choice in ("n", "no", "cancel"):
            print("❌ Message cancelled. Nothing was sent to Genesis.")
            return False, ""
        else:
            print("Please enter 'y' to approve, 'e' to edit, or 'n' to cancel.")


# Backwards compatibility alias for existing test suites
prompt_fonaut_approval = prompt_moderator_approval


def main():
    """Interactive CLI runner for the multi-party conversation interface."""
    print("=" * 65)
    print("✨ Genesis Multi-Party Conversation Interface")
    print("   Participants: ChatGPT • Antigravity • Genesis • Fodoh")
    print("=" * 65)

    # Initialize components
    ai = LLM()
    planner = Planner()
    brain = Brain()
    identity = Identity()
    tracker = LatencyTracker(log_path="logs/latency_converse.jsonl")
    session = MultiAgentSession(log_dir="logs")

    enable_voice = False
    voice_pref = input("\nWould you like Genesis to speak aloud via voice? ([y]/[N]): ").strip().lower()
    if voice_pref in ("y", "yes"):
        enable_voice = True
        print("🔊 Voice output enabled.")
    else:
        print("🔇 Text-only mode enabled.")

    while True:
        print("\n" + "-" * 40)
        print("Choose an action:")
        print(" [1] Send ChatGPT's Introduction")
        print(" [2] Send Antigravity's Introduction")
        print(" [3] Compose Message as Fodoh")
        print(" [4] Compose Message as ChatGPT")
        print(" [5] Compose Message as Antigravity")
        print(" [6] View Complete Session Transcript")
        print(" [7] Exit")
        print("-" * 40)

        choice = input("Select an option (1-7): ").strip()

        if choice == "1":
            speaker = "ChatGPT"
            message = PRESET_INTRODUCTIONS["ChatGPT"]
            approved, final_msg = prompt_moderator_approval(speaker, message)
            if approved:
                process_turn(session, speaker, final_msg, ai, planner, brain, identity, tracker, enable_voice)

        elif choice == "2":
            speaker = "Antigravity"
            message = PRESET_INTRODUCTIONS["Antigravity"]
            approved, final_msg = prompt_moderator_approval(speaker, message)
            if approved:
                process_turn(session, speaker, final_msg, ai, planner, brain, identity, tracker, enable_voice)

        elif choice in ("3", "4", "5"):
            speaker_map = {"3": "Fodoh", "4": "ChatGPT", "5": "Antigravity"}
            speaker = speaker_map[choice]

            print(f"\nEnter message from [{speaker}] (enter message text, press Enter twice to finish):")
            lines = []
            while True:
                line = input()
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            raw_msg = "\n".join(lines).strip()

            if not raw_msg:
                print("No message entered; cancelled.")
                continue

            approved, final_msg = prompt_fonaut_approval(speaker, raw_msg)
            if approved:
                process_turn(session, speaker, final_msg, ai, planner, brain, identity, tracker, enable_voice)

        elif choice == "6":
            session.print_transcript()

        elif choice == "7":
            print("\n👋 Exiting conversation interface. Session saved.")
            session.print_transcript()
            break
        else:
            print("Invalid option. Please choose 1 through 7.")


if __name__ == "__main__":
    main()
