"""Read-only, authenticated bridge for a Genesis project.

Run from the Genesis project root. Binds to localhost by default.
Use a secure HTTPS tunnel only after setting a strong GENESIS_BRIDGE_TOKEN.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parent
TOKEN = os.getenv("GENESIS_BRIDGE_TOKEN", "").strip()

app = FastAPI(
    title="Genesis Read-Only Bridge",
    version="0.1.0",
    description="Authenticated, read-only access to selected Genesis project information.",
)

# Allow only source, documentation, and plain-text configuration files.
# JSON is deliberately excluded because Genesis stores private data in JSON files.
ALLOWED_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml"}

# Never expose these directories, including their contents.
BLOCKED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "chroma_db",
    ".vscode",
    "identity",
    "memory",
    "conversation",
    "journal",
    "personality",
    "learning",
    "environment",
    "experiments",
    "tests",
}

# Never expose these filenames.
BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".bridge_token",
    "secrets.json",
    "credentials.json",
}

MAX_READ_BYTES = 20_000


def require_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Require the bridge's bearer token for every endpoint."""
    if not TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Bridge is not configured: set GENESIS_BRIDGE_TOKEN.",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required.")

    supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(status_code=403, detail="Invalid token.")


def is_blocked(relative_path: Path) -> bool:
    """Return True if any path component is private or hidden."""
    for part in relative_path.parts:
        if part.startswith(".") or part.lower() in BLOCKED_PARTS:
            return True

    if relative_path.name.lower() in BLOCKED_NAMES:
        return True

    return False


def safe_path(relative_path: str) -> Path:
    """Resolve an allowed relative path within the Genesis project."""
    candidate = Path(relative_path)

    if candidate.is_absolute() or ".." in candidate.parts:
        raise HTTPException(
            status_code=400,
            detail="Use a relative path inside the Genesis project.",
        )

    if not candidate.parts or is_blocked(candidate):
        raise HTTPException(
            status_code=403,
            detail="This path is not exposed by the bridge.",
        )

    if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=403,
            detail="That file type is not exposed by the bridge.",
        )

    resolved = (PROJECT_ROOT / candidate).resolve()

    # Prevent path traversal and symlinks from escaping the project root.
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise HTTPException(
            status_code=403,
            detail="Path is outside the project.",
        )

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    # Check the resolved path as well, in case a symlink points into a
    # private folder or to a hidden file.
    resolved_relative = resolved.relative_to(PROJECT_ROOT)
    if is_blocked(resolved_relative):
        raise HTTPException(
            status_code=403,
            detail="This path is not exposed by the bridge.",
        )

    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=403,
            detail="That file type is not exposed by the bridge.",
        )

    return resolved


@app.get("/health", dependencies=[Depends(require_token)])
def health() -> dict[str, str]:
    """Report bridge health without revealing project contents."""
    return {
        "service": "Genesis Read-Only Bridge",
        "status": "ok",
        "mode": "read-only",
    }


@app.get("/project/files", dependencies=[Depends(require_token)])
def list_project_files() -> dict[str, Any]:
    """List only files permitted by the bridge's allowlist."""
    items: list[str] = []

    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue

        try:
            relative = path.relative_to(PROJECT_ROOT)
        except ValueError:
            continue

        if is_blocked(relative):
            continue

        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue

        # Resolve symlinks and ensure they remain within the project.
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(PROJECT_ROOT):
                continue

            resolved_relative = resolved.relative_to(PROJECT_ROOT)
            if is_blocked(resolved_relative):
                continue

            if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
        except (OSError, ValueError):
            continue

        items.append(relative.as_posix())

    return {
        "root": PROJECT_ROOT.name,
        "files": sorted(items)[:250],
    }


@app.get("/project/read", dependencies=[Depends(require_token)])
def read_project_file(
    path: str = Query(..., min_length=1, max_length=240),
) -> JSONResponse:
    """Read an allowed UTF-8 text file, with a strict size limit."""
    target = safe_path(path)

    try:
        data = target.read_bytes()
    except OSError:
        raise HTTPException(status_code=500, detail="Unable to read file.")

    if len(data) > MAX_READ_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large to read through bridge "
            f"(limit {MAX_READ_BYTES} bytes).",
        )

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail="File is not UTF-8 text.",
        )

    return JSONResponse(
        {
            "path": path,
            "content": content,
        }
    )