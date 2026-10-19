import json
from pathlib import Path


class History:

    def __init__(self):
        self.file = Path("conversation/history.json")

        if not self.file.exists():
            self.file.write_text("[]")

    def load(self):

        text = self.file.read_text().strip()

        if text == "":
            return []

        return json.loads(text)

    def save(self, history):

        self.file.write_text(
            json.dumps(history, indent=4)
        )

    def add(self, role, message):

        history = self.load()

        history.append({
            "role": role,
            "content": message
        })

        self.save(history)

    def recent(self, limit=8):

        return self.load()[-limit:]