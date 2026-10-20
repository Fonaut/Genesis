"""
Genesis Communication Interface — FastAPI Local Web Server
Binds strictly to 127.0.0.1 (localhost).
Integrates the Communication Hub, approval gates, kill switch, and Development Journal.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from interface.engine import CommunicationInterfaceEngine
from interface.models import (
    IndividualChatRequest,
    TriadInitiateRequest,
    TriadGateResponseRequest,
    KillSwitchRequest,
    JournalActionRequest,
)
from hub.phase0 import HubSafetyError, KillSwitchActiveError
from hub.phase1 import CostLimitExceededError

STATIC_DIR = CURRENT_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(
    title="Genesis Communication Interface",
    description="Local browser-based communication interface for Genesis, Antigravity, and OpenAI-Agent.",
    version="1.0.0",
)

# Enforce localhost-only access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = CommunicationInterfaceEngine()

# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# -----------------------------------------------------------------------------
# Frontend Root
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def get_index():
    if not INDEX_HTML.exists():
        return HTMLResponse(
            "<html><body><h1>Genesis Communication Interface</h1><p>index.html not found.</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(content=INDEX_HTML.read_text(encoding="utf-8"), status_code=200)


# -----------------------------------------------------------------------------
# System & Engine Status
# -----------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    triad = engine.active_triad_session
    triad_data = None
    if triad:
        triad_data = {
            "session_id": triad.session_id,
            "status": triad.status,
            "is_live": triad.is_live,
            "turns_count": len(triad.turns),
            "pending_gate": triad.pending_gate.__dict__ if triad.pending_gate else None,
            "cumulative_cost_usd": round(triad.cumulative_cost_usd, 6),
            "error_message": triad.error_message,
        }

    return {
        "kill_switch_active": engine.is_kill_switch_active,
        "max_cost_usd": engine.max_cost_usd,
        "cumulative_cost_usd": round(engine.governor.cumulative_cost_usd, 6),
        "active_triad_session": triad_data,
        "participants": [
            {"id": "Genesis", "name": "Genesis", "role": "Voice Companion & Orchestrator", "color": "purple"},
            {"id": "Antigravity", "name": "Antigravity", "role": "Technical Co-Architect & Execution Engine", "color": "blue"},
            {"id": "OpenAI-Agent", "name": "OpenAI-Agent", "role": "Analytical Specialist & Critical Challenger", "color": "emerald"},
        ],
    }


# -----------------------------------------------------------------------------
# Emergency Stop (Kill Switch)
# -----------------------------------------------------------------------------

@app.post("/api/kill-switch")
async def handle_kill_switch(req: KillSwitchRequest):
    if req.action == "trigger":
        result = engine.trigger_kill_switch()
        return JSONResponse(status_code=200, content=result)
    elif req.action == "clear":
        result = engine.clear_kill_switch()
        return JSONResponse(status_code=200, content=result)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'. Must be 'trigger' or 'clear'.")


# -----------------------------------------------------------------------------
# Individual 1-on-1 Chat
# -----------------------------------------------------------------------------

@app.post("/api/chat/individual")
async def chat_individual(req: IndividualChatRequest):
    try:
        turn = engine.send_individual_message(
            recipient=req.recipient,
            message=req.message,
            is_live=req.is_live,
        )
        return turn
    except (KillSwitchActiveError, HubSafetyError, CostLimitExceededError) as err:
        raise HTTPException(status_code=400, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Internal agent execution error: {str(err)}")


# -----------------------------------------------------------------------------
# Supervised Triad Discussion (Phase 1 Routing)
# -----------------------------------------------------------------------------

@app.post("/api/chat/triad/initiate")
async def initiate_triad(req: TriadInitiateRequest):
    try:
        state = engine.initiate_triad_round(
            probe_prompt=req.probe_prompt,
            is_live=req.is_live,
        )
        return {
            "session_id": state.session_id,
            "status": state.status,
            "is_live": state.is_live,
            "probe_prompt": state.probe_prompt,
            "pending_gate": state.pending_gate.__dict__ if state.pending_gate else None,
            "turns": state.turns,
            "cumulative_cost_usd": state.cumulative_cost_usd,
        }
    except (KillSwitchActiveError, HubSafetyError) as err:
        raise HTTPException(status_code=400, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))


@app.get("/api/chat/triad/status")
async def get_triad_status():
    state = engine.active_triad_session
    if not state:
        return {"status": "IDLE", "session": None}

    return {
        "status": state.status,
        "session_id": state.session_id,
        "is_live": state.is_live,
        "probe_prompt": state.probe_prompt,
        "turns": state.turns,
        "pending_gate": state.pending_gate.__dict__ if state.pending_gate else None,
        "cumulative_cost_usd": round(state.cumulative_cost_usd, 6),
        "error_message": state.error_message,
    }


@app.post("/api/chat/triad/respond-gate")
async def respond_triad_gate(req: TriadGateResponseRequest):
    try:
        state = engine.respond_triad_gate(
            approval_id=req.approval_id,
            action=req.action,
            edited_payload=req.edited_payload,
        )
        return {
            "session_id": state.session_id,
            "status": state.status,
            "turns": state.turns,
            "pending_gate": state.pending_gate.__dict__ if state.pending_gate else None,
            "cumulative_cost_usd": round(state.cumulative_cost_usd, 6),
            "error_message": state.error_message,
        }
    except (KillSwitchActiveError, HubSafetyError) as err:
        raise HTTPException(status_code=400, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))


# -----------------------------------------------------------------------------
# Development Journal Integration
# -----------------------------------------------------------------------------

@app.get("/api/journal/status")
async def get_journal_status():
    return engine.journal_manager.get_status()


@app.get("/api/journal/report")
async def get_journal_report():
    return engine.journal_manager.get_report()


@app.post("/api/journal/action")
async def handle_journal_action(req: JournalActionRequest):
    mgr = engine.journal_manager
    action = req.action.lower()
    try:
        if action == "start":
            sess = mgr.start_session(activity=req.activity or "", milestone=req.milestone or "")
            return {"status": "OK", "action": "start", "session": sess.to_dict()}
        elif action == "break":
            brk = mgr.take_break(reason=req.reason or "")
            return {"status": "OK", "action": "break", "break": brk.to_dict()}
        elif action == "resume":
            sess = mgr.resume_session()
            return {"status": "OK", "action": "resume", "session": sess.to_dict()}
        elif action == "stop":
            sess = mgr.stop_session(summary=req.summary or "")
            return {"status": "OK", "action": "stop", "session": sess.to_dict()}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown journal action '{action}'.")
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))


# -----------------------------------------------------------------------------
# Conversation History
# -----------------------------------------------------------------------------

@app.get("/api/history")
async def get_conversation_history():
    history_file = engine.conversations_file
    if not history_file.exists():
        return []
    try:
        import json
        return json.loads(history_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def run(host: str = "127.0.0.1", port: int = 8765):
    """Launches the Genesis Communication Interface web server."""
    import uvicorn
    kill_switch_state = "TRIGGERED (.hub_stopped present)" if (PROJECT_ROOT / ".hub_stopped").exists() else "Ready (Inactive)"
    print("=" * 75, flush=True)
    print("🚀 GENESIS COMMUNICATION INTERFACE", flush=True)
    print(f"   Local URL:    http://{host}:{port}", flush=True)
    print(f"   Binding:      {host} (Localhost Only)", flush=True)
    print(f"   Storage:      logs/interface/ & logs/journal/", flush=True)
    print(f"   Kill Switch:  {kill_switch_state}", flush=True)
    print("=" * 75, flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
