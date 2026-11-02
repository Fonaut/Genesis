# Genesis — Persistent AI Companion & Multi-Agent Orchestration Hub

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Local%20Gateway-009688.svg)](https://fastapi.tiangolo.com)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-Vector%20Store-orange.svg)](https://www.trychroma.com)
[![Testing](https://img.shields.io/badge/Tests-175%20Passing%20(100%25%20Offline)-brightgreen.svg)](tests/)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

Genesis is a modular Python artificial intelligence architecture designed for **long-term memory persistence**, **context-aware planning**, and **supervised multi-agent collaboration**. Rather than acting as a stateless chatbot, Genesis maintains continuous identity, retrieves context-relevant memories across process restarts, and orchestrates structured communication between disparate LLM agents through a local security gateway.

---

## Architecture Overview

```
                      ┌─────────────────────────────────────────┐
                      │        User / Supervisor (Fodoh)        │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼
                      ┌─────────────────────────────────────────┐
                      │   Communication Interface (FastAPI)     │
                      │   - Human Approval Gates (Nonce)        │
                      │   - Anti-Spoofing Directives            │
                      │   - Pre-Dispatch Budget Reservations    │
                      └────────────┬────────────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
         ▼                         ▼                         ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Genesis Local    │      │ OpenAI-Agent     │      │ Antigravity      │
│ Agent & Planner  │      │ (GPT-4o-mini)    │      │ (Gemini Flash)   │
└────────┬─────────┘      └──────────────────┘      └──────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Genesis Cognitive Subsystems                      │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Intent Planner   │  │ Dual-Layer Brain │  │ Identity Manager   │  │
│  │ (Search vs Save) │  │ (ChromaDB + JSON)│  │ (identity/self)    │  │
│  └──────────────────┘  └──────────────────┘  └────────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Memory Review    │  │ Unified Cost     │  │ Dev Journal        │  │
│  │ Queue & Staging  │  │ Ledger & Quotas  │  │ Time & Break Track │  │
│  └──────────────────┘  └──────────────────┘  └────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Key Capabilities & Implemented Subsystems

### 1. Dual-Layer Persistent Memory (`memory/brain.py`)
- **Semantic Vector Store:** Powered by **ChromaDB** for embedding-based similarity search over long-term historical interactions.
- **Deduplication & Cold-Start Persistence:** Verified across independent OS process launches with 100% blind retrieval accuracy. Dialogue history can be completely cleared without losing underlying traits.
- **Cryptographic Safeguards:** State stores enforce byte-for-byte **SHA-256 baseline hashing** (`memory/memories.json`, `conversation/history.json`) to prevent unintended state mutation during testing or synthetic sessions.

### 2. Context-Aware Intent Planning (`core/planner.py`)
- Decouples dialogue orchestration from execution.
- Evaluates incoming prompts to determine whether to trigger vector memory search, persist new stable user traits, or proceed with standard response synthesis, keeping prompt sizes compact and token costs low.

### 3. Multi-Agent Communication Hub & Security Gateway (`hub/`, `interface/`)
- **Supervised Triad Orchestration:** Enables structured multi-round collaboration between Genesis, OpenAI agents, and Gemini/Antigravity.
- **Human-in-the-Loop Approval Gates:** Employs single-use cryptographic nonces for message dispatches, allowing the operator to inspect, edit, or reject intermediate agent turns.
- **Pre-Dispatch Budget Reservations:** Atomically calculates and reserves token cost ceilings prior to initiating external API requests, preventing runaway expenditure.
- **Collaborative Memory Staging:** Incoming multi-agent consensus items are routed to a human review queue (`collaborative_staging.json`) rather than writing directly to Genesis's personal memory store.

### 4. Grounded Capabilities & Sensory Honesty (`core/capabilities.py`)
- Implements capability boundaries ensuring the system transparently reports its operational envelope (e.g., explicitly acknowledging the absence of cameras, computer vision, or ambient sensors rather than simulating perception).

---

## Automated Test Suite (175 Tests, 100% Offline)

Genesis includes an extensive automated regression test suite executed via `pytest`. **All tests run 100% offline** using deterministic mocks, ensuring zero paid API tokens and zero network leakage:

```bash
# Set PYTHONPATH to project root and execute full suite
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

### Verified Test Breakdown (175 Passed):
- `tests/test_brain.py` (8 tests): JSON fallback loading, semantic recall, duplicate rejection, whitespace handling.
- `tests/test_capabilities.py` (4 tests): Capability manifest validation, hardware boundary enforcement.
- `tests/test_collaborative_memory.py` (21 tests): Human review queue lifecycle, anti-spoofing memory isolation, rollback and backup.
- `tests/test_converse.py` (9 tests): Approval gating, prompt formatting, memory isolation across assistants.
- `tests/test_dev_journal.py` (17 tests): Session time calculation, break tracking, midnight-boundary day splitting, crash recovery.
- `tests/test_interface.py` (35 tests): FastAPI local endpoints, cost ledger reconciliation, safety governor, HTML audit reporting.
- `tests/test_memory_continuity.py` (4 tests): Cold-start process restart persistence, zero-hint blind recall, clean history verification.
- `tests/test_metrics.py` (5 tests): Latency tracking, percentile calculation, token profiling.
- `tests/test_multi_round_collaboration.py` (20 tests): Multi-round session state machines, human interjections, recovery transitions.
- `tests/test_phase0_poc.py` (19 tests): Process isolation, Seatbelt sandboxing, mock CLI exchanges.
- `tests/test_phase1_hub.py` (21 tests): Three-agent hub orchestration, cost caps, turn truncation safeguards.
- `tests/test_planner.py` (4 tests): Intent classification for memory search and ingestion.
- `tests/test_wake_word.py` (8 tests): Audio wake-word parsing, reasoning privacy, one-shot commands.

---

## Directory Layout

```
Genesis/
├── core/                   # Core agent logic
│   ├── capabilities.py     # Hardware & capability manifests
│   ├── config.py           # Runtime configuration & paths
│   ├── llm.py              # LLM client abstractions
│   ├── metrics.py          # Latency & performance profiling
│   ├── planner.py          # Intent classification & routing
│   └── wake_word.py        # Voice wake-word parsing
├── memory/                 # Persistence layer
│   ├── brain.py            # ChromaDB + JSON memory coordinator
│   ├── collaborative.py    # Staging queue & human review manager
│   └── models.py           # Data models for experiences & memories
├── hub/                    # Multi-agent coordination
│   ├── phase0.py           # Isolated PoC orchestration engine
│   └── phase1.py           # Supervised 3-agent collaborative hub
├── interface/              # Web & API Gateway
│   ├── engine.py           # Gateway engine & cost reconciler
│   ├── group_session.py    # Multi-round session state manager
│   ├── models.py           # Pydantic request/response schemas
│   ├── server.py           # FastAPI local server (127.0.0.1)
│   └── static/             # Web dashboard (HTML/JS/CSS)
├── journal/                # Engineering productivity & time tracking
│   ├── manager.py          # Development journal & break accountant
│   └── models.py           # Session & interval data models
├── docs/                   # Architectural audits & milestone reports
└── tests/                  # 175 offline automated unit & regression tests
```

---

## Getting Started

### Prerequisites
- Python 3.12 or newer
- Virtual environment (`venv`)

### Installation & Local Setup

```bash
# Clone the repository
git clone https://github.com/Fonaut/Genesis.git
cd Genesis

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements-bridge.txt
pip install fastapi uvicorn pydantic chromadb pytest

# Run the test suite
PYTHONPATH=. pytest tests/
```

### Launching the Local Gateway
```bash
# Starts the local dashboard strictly on localhost (127.0.0.1:8000)
python3 interface/server.py
```

---

## Engineering Limitations & Roadmap

- **Memory Scoring:** Currently utilizes vector cosine similarity and keyword matching; importance-weighted decay curves and reflection passes are in active research.
- **Model Agility:** The local gateway supports OpenAI-compatible endpoints and Gemini adapters; native support for local open-weights inference (via Ollama or llama.cpp) is planned.
- **Tool System:** Function calling is currently gated through supervised dispatch; an automated sandboxed code execution tool is in staging.

---

## Author & Maintainer

**Fodoh C. Adamah**  
*BSBA Management Information Systems, Winston-Salem State University*  
GitHub: [@Fonaut](https://github.com/Fonaut) • LinkedIn: [/in/fodohadamah](https://linkedin.com/in/fodohadamah)
