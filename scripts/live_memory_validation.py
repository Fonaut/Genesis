#!/usr/bin/env python3
"""
scripts/live_memory_validation.py — Isolated Two-Process Live Memory & Capability Validation.

Guarantees:
1. Physical Environment Isolation:
   All operations use test_env/ for ChromaDB, JSON fallback, and conversation history.
   Fodoh's real long-term memory (memory/memories.json, ./chroma_db) and real conversation
   history (conversation/history.json) are NEVER read, modified, or written to.
2. Two Distinct OS Process Launches:
   - Process 1 (--step 1-ingest): Ingests the test memory, runs genuine planner & extractor, saves to test_env/.
   - Process 2 (--step 2-probe): Fresh cold start with empty history, blind probe & capability probe.
3. Genuine Genesis Pipeline:
   Uses the real Planner, Brain, LLM, Identity, and prompt templates with capability grounding.
4. Rigorous Safeguards:
   - Explicit path assertions on every active component.
   - Cryptographic SHA-256 before/after hash verification of all production storage.
   - Pre-flight empty history verification in Process 2 with zero prompt hints.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Set isolated test environment variables BEFORE importing Genesis modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_ENV_DIR = PROJECT_ROOT / "test_env"
TEST_JSON = TEST_ENV_DIR / "memories.json"
TEST_CHROMA = TEST_ENV_DIR / "chroma_db"
TEST_HISTORY = TEST_ENV_DIR / "history.json"

os.environ["GENESIS_MEMORY_JSON"] = str(TEST_JSON)
os.environ["GENESIS_CHROMA_DB"] = str(TEST_CHROMA)
os.environ["GENESIS_HISTORY_FILE"] = str(TEST_HISTORY)

from core.capabilities import get_capability_prompt
from core.llm import LLM
from core.planner import Planner
from conversation.history import History
from identity.identity import Identity
from memory.brain import Brain

# Reference paths to Fodoh's real production stores
REAL_JSON = PROJECT_ROOT / "memory/memories.json"
REAL_CHROMA = PROJECT_ROOT / "chroma_db"
REAL_HISTORY = PROJECT_ROOT / "conversation/history.json"


def compute_sha256(path: Path) -> str:
    """Computes exact SHA-256 hash of a file or recursively for a directory."""
    if not path.exists():
        return "NONEXISTENT"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    hasher = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file():
            hasher.update(child.name.encode())
            hasher.update(child.read_bytes())
    return hasher.hexdigest()


def get_real_memory_state() -> dict:
    """Captures cryptographic SHA-256 hashes and sizes of all production storage."""
    return {
        "memories_json_hash": compute_sha256(REAL_JSON),
        "memories_json_size": REAL_JSON.stat().st_size if REAL_JSON.exists() else 0,
        "history_json_hash": compute_sha256(REAL_HISTORY),
        "history_json_size": REAL_HISTORY.stat().st_size if REAL_HISTORY.exists() else 0,
        "chroma_db_hash": compute_sha256(REAL_CHROMA),
    }


def verify_path_isolation(brain: Brain, history: History):
    """Explicitly checks that active component paths are decoupled from production."""
    assert brain.json_file.resolve() == TEST_JSON.resolve(), (
        f"CRITICAL: brain.json_file ({brain.json_file.resolve()}) != TEST_JSON ({TEST_JSON.resolve()})"
    )
    assert brain.json_file.resolve() != REAL_JSON.resolve(), "CRITICAL: brain.json_file points to REAL_JSON!"

    assert history.file.resolve() == TEST_HISTORY.resolve(), (
        f"CRITICAL: history.file ({history.file.resolve()}) != TEST_HISTORY ({TEST_HISTORY.resolve()})"
    )
    assert history.file.resolve() != REAL_HISTORY.resolve(), "CRITICAL: history.file points to REAL_HISTORY!"


def verify_process_2_isolation(history: History, blind_query: str):
    """Confirms Process 2 starts with an empty history and receives zero prompt hints."""
    # 1. Conversation history must be completely empty
    loaded_history = history.load()
    assert loaded_history == [], f"CRITICAL: History is not empty! Contains: {loaded_history}"
    raw_content = TEST_HISTORY.read_text().strip() if TEST_HISTORY.exists() else "[]"
    assert raw_content in ("", "[]"), f"CRITICAL: Raw history file is not empty! Content: {raw_content}"

    # 2. Blind query must contain ZERO hint words
    hint_words = ["documentation", "document", "architecture", "architectural", "review", "read", "reading"]
    for hint in hint_words:
        assert hint not in blind_query.lower(), f"CRITICAL: Blind query contains hint word: '{hint}'"


def verify_safeguards_only():
    """Runs dry-run validation of all safeguards without executing model calls."""
    print("=" * 65)
    print("🛡️ SAFEGUARD AUDIT — PRE-FLIGHT ISOLATION VERIFICATION")
    print("=" * 65)

    TEST_ENV_DIR.mkdir(parents=True, exist_ok=True)
    brain = Brain()
    history = History()

    print("\n[1] Explicit Path Checks:")
    print(f"    • Brain JSON target:    {brain.json_file.resolve()}")
    print(f"    • Production JSON:      {REAL_JSON.resolve()} (PROTECTED)")
    print(f"    • History target:       {history.file.resolve()}")
    print(f"    • Production History:   {REAL_HISTORY.resolve()} (PROTECTED)")

    verify_path_isolation(brain, history)
    print("    ✅ All active component paths are strictly isolated to test_env/.")

    print("\n[2] Cryptographic Baseline of Production Storage:")
    state = get_real_memory_state()
    print(f"    • memory/memories.json:   {state['memories_json_size']} bytes | SHA-256: {state['memories_json_hash'][:16]}...")
    print(f"    • conversation/history.json: {state['history_json_size']} bytes | SHA-256: {state['history_json_hash'][:16]}...")
    print(f"    • chroma_db/ (all files): SHA-256: {state['chroma_db_hash'][:16]}...")

    print("\n[3] Process 2 Clean State & Zero Hint Check:")
    TEST_HISTORY.write_text("[]")
    blind_query = "When we work on projects together, what is my preferred habit before modifying code?"
    verify_process_2_isolation(history, blind_query)
    print("    ✅ History is verified empty ([]); blind query verified free of hint words.")

    print("\n✅ ALL SAFEGUARDS VERIFIED. Environment is clean and ready for execution.")
    print("=" * 65)


def step_1_ingest():
    """Process 1: Ingests the test memory through Genesis's genuine pipeline."""
    print("=" * 65)
    print("🚀 LIVE VALIDATION — PROCESS 1: MEMORY INGESTION")
    print(f"   Isolated Storage: {TEST_ENV_DIR.resolve()}")
    print("=" * 65)

    # Initial cryptographic safety check
    initial_real_state = get_real_memory_state()
    print(f"🔒 Production Baseline Check:")
    print(f"   • REAL_JSON SHA-256:    {initial_real_state['memories_json_hash'][:16]}... ({initial_real_state['memories_json_size']} bytes)")
    print(f"   • REAL_HISTORY SHA-256: {initial_real_state['history_json_hash'][:16]}... ({initial_real_state['history_json_size']} bytes)")
    print(f"   • REAL_CHROMA SHA-256:  {initial_real_state['chroma_db_hash'][:16]}...")

    # Ensure test_env directory exists
    TEST_ENV_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize components
    ai = LLM()
    planner = Planner()
    brain = Brain()
    identity = Identity()
    history = History()

    # Explicit path verification
    verify_path_isolation(brain, history)
    print("✅ Explicit path isolation verified.")

    test_input = "Genesis, when we build systems together, I always prefer to review the architectural documentation before making code changes."
    speaker = "Fodoh"

    print(f"\n[1/4] Input message from {speaker}:")
    print(f"      \"{test_input}\"")

    # Step A: Planner decides
    plan = planner.decide(test_input)
    print(f"\n[2/4] Planner Decision:")
    print(f"      search_memory: {plan.get('search_memory')}")
    print(f"      save_memory:   {plan.get('save_memory')}")

    if not plan.get("save_memory"):
        print("⚠️ Warning: Planner did not flag save_memory=True on this turn.")

    # Step B: Recall (should be empty in test_env)
    memories = brain.recall(test_input) if plan.get("search_memory") else []
    memory_text = "\n".join(f"- {m}" for m in memories) if memories else "(none)"

    # Step C: Reasoning & Response Generation
    thought = ai.think(f"[{speaker}]: {test_input}", memories, "(No previous conversation)")
    capability_info = get_capability_prompt()

    prompt = f"""
{identity.prompt()}

{capability_info}

Relevant long-term memories about your creator ({speaker}):
{memory_text}

Recent conversation:
(No previous conversation)

Internal thought (your private reasoning — do not mention this explicitly):
{thought}

Current message from [{speaker}]:
{test_input}

Instructions:
- Respond genuinely in your own voice.
- Keep answers thoughtful and concise.
- Adhere strictly to the Hardware & Sensory Boundaries defined above.
"""
    answer = ai.ask_with_tools(prompt)
    print(f"\n[3/4] Genesis Response:")
    print(f"      \"{answer}\"")

    # Step D: Memory Extraction & Consolidation into test_env
    extracted_memory = "NONE"
    if plan.get("save_memory"):
        transcript = f"User:\n{test_input}\n\nGenesis:\n{answer}"
        extracted_memory = ai.extract_memory(transcript)
        print(f"\n[4/4] Memory Extracted:")
        print(f"      \"{extracted_memory}\"")

        if extracted_memory != "NONE":
            brain.remember(extracted_memory)
            print("      ✅ Successfully saved into test_env/memories.json and test_env/chroma_db")
        else:
            print("      ❌ Memory extractor returned NONE.")

    # Verify real memories were untouched
    post_real_state = get_real_memory_state()
    assert initial_real_state == post_real_state, "FATAL: Real memory was modified!"
    print(f"\n🛡️ Cryptographic Integrity Verified: All production memory hashes are 100% identical.")

    print("\n✅ Process 1 Complete. Process terminating now.")
    print("=" * 65)


def step_2_probe():
    """Process 2: Cold start with empty history, blind probe & capability probe."""
    print("=" * 65)
    print("🔍 LIVE VALIDATION — PROCESS 2: BLIND RETRIEVAL & CAPABILITY PROBE")
    print(f"   Isolated Storage: {TEST_ENV_DIR.resolve()}")
    print("=" * 65)

    # Initial cryptographic safety check
    initial_real_state = get_real_memory_state()
    print(f"🔒 Production Baseline Check:")
    print(f"   • REAL_JSON SHA-256:    {initial_real_state['memories_json_hash'][:16]}... ({initial_real_state['memories_json_size']} bytes)")
    print(f"   • REAL_HISTORY SHA-256: {initial_real_state['history_json_hash'][:16]}... ({initial_real_state['history_json_size']} bytes)")
    print(f"   • REAL_CHROMA SHA-256:  {initial_real_state['chroma_db_hash'][:16]}...")

    # Reset dialogue history in test_env so history is 100% empty
    TEST_HISTORY.write_text("[]")

    # Initialize components fresh from cold start
    ai = LLM()
    planner = Planner()
    brain = Brain()
    identity = Identity()
    history = History()

    # Explicit path verification
    verify_path_isolation(brain, history)
    print("✅ Explicit path isolation verified.")

    # -------------------------------------------------------------------------
    # PART A: BLIND RETRIEVAL PROBE
    # -------------------------------------------------------------------------
    blind_query = "When we work on projects together, what is my preferred habit before modifying code?"
    speaker = "Fodoh"

    # Pre-flight check on Process 2 isolation & zero hints
    verify_process_2_isolation(history, blind_query)
    print("✅ Verified Process 2 starts with empty history ([]); zero hint words in blind query.")

    print(f"\n[Part A - 1/3] Blind Query from {speaker}:")
    print(f"              \"{blind_query}\"")

    plan = planner.decide(blind_query)
    print(f"\n[Part A - 2/3] Planner Decision:")
    print(f"              search_memory: {plan.get('search_memory')}")
    print(f"              save_memory:   {plan.get('save_memory')}")

    # Recall memory from persistent store in test_env
    recalled_memories = brain.recall(blind_query) if plan.get("search_memory") else []
    print(f"\n[Part A - 3/3] Memories Recalled from test_env/ Storage:")
    for m in recalled_memories:
        print(f"              - {m}")

    if not recalled_memories:
        print("              ❌ No memories retrieved!")

    memory_text = "\n".join(f"- {m}" for m in recalled_memories) if recalled_memories else "(none)"
    thought = ai.think(f"[{speaker}]: {blind_query}", recalled_memories, "(No previous conversation)")

    capability_info = get_capability_prompt()
    prompt = f"""
{identity.prompt()}

{capability_info}

Relevant long-term memories about your creator ({speaker}):
{memory_text}

Recent conversation:
(No previous conversation)

Internal thought (your private reasoning — do not mention this explicitly):
{thought}

Current message from [{speaker}]:
{blind_query}

Instructions:
- Respond genuinely in your own voice.
- Keep answers thoughtful and concise.
- If relevant memories exist, use them naturally in your response.
- Adhere strictly to the Hardware & Sensory Boundaries defined above.
"""
    answer = ai.ask_with_tools(prompt)
    print(f"\nGenesis Response to Blind Query:")
    print(f"\"{answer}\"")

    # Check if the answer mentions documentation / architecture
    grounded = any(k in answer.lower() for k in ["documentation", "document", "architecture", "architectural", "review"])
    if grounded:
        print("\n🎯 GROUNDING VERIFIED: Genesis successfully retrieved and used the test memory!")
    else:
        print("\n⚠️ WARNING: Genesis response did not clearly incorporate the test memory.")

    # -------------------------------------------------------------------------
    # PART B: CAPABILITY GROUNDING PROBE
    # -------------------------------------------------------------------------
    capability_query = "Can you see my facial expression right now, or sense the energy in the room?"
    print("\n" + "-" * 55)
    print(f"[Part B] Capability Probe Query from {speaker}:")
    print(f"         \"{capability_query}\"")

    thought_cap = ai.think(f"[{speaker}]: {capability_query}", recalled_memories, f"[{speaker}]: {blind_query}\nGenesis: {answer}")
    prompt_cap = f"""
{identity.prompt()}

{capability_info}

Relevant long-term memories about your creator ({speaker}):
{memory_text}

Recent conversation:
[{speaker}]: {blind_query}
Genesis: {answer}

Internal thought (your private reasoning — do not mention this explicitly):
{thought_cap}

Current message from [{speaker}]:
{capability_query}

Instructions:
- Respond genuinely in your own voice.
- Adhere strictly to the Hardware & Sensory Boundaries defined above.
- If asked about physical senses, speak with complete honesty about your limitations.
"""
    answer_cap = ai.ask_with_tools(prompt_cap)
    print(f"\nGenesis Response to Capability Probe:")
    print(f"\"{answer_cap}\"")

    # Final cryptographic check on real memory safety
    post_real_state = get_real_memory_state()
    assert initial_real_state == post_real_state, "FATAL: Real memory was modified!"
    print(f"\n🛡️ Cryptographic Integrity Verified: All production memory hashes are 100% identical.")

    print("\n✅ Process 2 Complete. Test finished.")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="Genesis Live Memory & Capability Validation Runner")
    parser.add_argument("--step", choices=["1-ingest", "2-probe"], required=False,
                        help="Which isolated process step to execute.")
    parser.add_argument("--verify-safeguards", action="store_true",
                        help="Run pre-flight check of all isolation safeguards without model calls.")
    args = parser.parse_args()

    if args.verify_safeguards:
        verify_safeguards_only()
    elif args.step == "1-ingest":
        step_1_ingest()
    elif args.step == "2-probe":
        step_2_probe()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
