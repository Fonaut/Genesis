
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("Genesis Read-Only")

BRIDGE_URL = "http://127.0.0.1:8765"
TOKEN_FILE = Path.home() / "Desktop" / "Genesis" / ".bridge_token"


def bridge_get(endpoint: str, params: dict | None = None) -> dict:
    """Make an authenticated request to the local read-only bridge."""
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()

    url = BRIDGE_URL + endpoint
    if params:
        url += "?" + urlencode(params)

    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@mcp.tool()
def genesis_status() -> dict:
    """Check whether the local Genesis bridge is running."""
    return bridge_get("/health")


@mcp.tool()
def genesis_list_files() -> dict:
    """List files that the Genesis bridge permits access to."""
    return bridge_get("/project/files")


@mcp.tool()
def genesis_read_file(path: str) -> dict:
    """Read an allowed text or source file inside Genesis. The bridge blocks secret files and paths outside the project."""
    return bridge_get("/project/read", {"path": path})


if __name__ == "__main__":
    mcp.run(transport="stdio")
