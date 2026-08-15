from datetime import datetime

from memory.memory import Memory


class Brain:
    def __init__(self):
        self.memories = []

    def remember(
        self,
        event,
        emotion="neutral",
        importance=1,
        category="general",
        tags=None,
    ):
        memory = Memory(
            event=event,
            emotion=emotion,
            importance=importance,
            created=datetime.now(),
            category=category,
            tags=tags or [],
        )

        self.memories.append(memory)

        return memory

    def recall_all(self):
        return self.memories

    def count(self):
        return len(self.memories)