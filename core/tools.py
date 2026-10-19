import subprocess
from datetime import datetime
from pathlib import Path


# ---------- Tool Implementations ----------

def get_current_time():
    """Return the current date and time."""
    now = datetime.now()
    return now.strftime("It is %A, %B %d, %Y at %I:%M %p.")


def open_application(app_name: str):
    """Open a macOS application by name (e.g. 'Safari', 'Spotify', 'Terminal')."""
    try:
        subprocess.Popen(["open", "-a", app_name])
        return f"Opened {app_name}."
    except Exception as e:
        return f"Could not open {app_name}: {e}"


def list_files(directory: str = "."):
    """List files in a directory."""
    try:
        path = Path(directory).expanduser()
        if not path.exists():
            return f"Directory {directory} does not exist."
        items = [f.name for f in path.iterdir()]
        if not items:
            return f"{directory} is empty."
        return f"Files in {directory}: " + ", ".join(items[:25])
    except Exception as e:
        return f"Error listing files: {e}"


def read_file(filepath: str):
    """Read the first 500 characters of a text file."""
    try:
        path = Path(filepath).expanduser()
        if not path.exists():
            return f"File {filepath} does not exist."
        content = path.read_text()[:500]
        return f"Contents of {filepath}:\n{content}"
    except Exception as e:
        return f"Error reading file: {e}"


# ---------- OpenAI Tool Schemas ----------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time on the user's computer.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open a macOS application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application, e.g. 'Safari', 'Spotify', 'Terminal', 'Notes'.",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory on the user's computer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Path to the directory, e.g. '~/Downloads' or '.'.",
                    }
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the first 500 characters of a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file, e.g. '~/Documents/todo.txt'.",
                    }
                },
                "required": ["filepath"],
            },
        },
    },
]


# ---------- Dispatch Table ----------

TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
    "open_application": open_application,
    "list_files": list_files,
    "read_file": read_file,
}