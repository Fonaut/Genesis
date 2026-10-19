import json
from pathlib import Path

MEMORY_FILE = Path("memory/memories.json")


class Memory:
    ...

    def remember(self, fact):
        self.add(fact)
    def __init__(self):
        if not MEMORY_FILE.exists():
            MEMORY_FILE.write_text("[]")

    def load(self):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)

    def save(self, memories):
        with open(MEMORY_FILE, "w") as f:
            json.dump(memories, f, indent=4)

    def add(self, text):
        memories = self.load()
        memories.append(text)
        self.save(memories)