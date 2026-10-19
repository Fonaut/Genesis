import json
from pathlib import Path

HISTORY_FILE = Path("conversation/history.json")


class Conversation:
    def __init__(self):
        if not HISTORY_FILE.exists():
            HISTORY_FILE.write_text("[]")

    def load(self):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)

    def save(self, history):
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)

    def add(self, role, content):
        history = self.load()

        history.append(
            {
                "role": role,
                "content": content
            }
        )

        self.save(history)

    def recent(self, limit=10):
        history = self.load()
        return history[-limit:]

    def clear(self):
        self.save([])