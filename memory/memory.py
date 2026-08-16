from memory.models import Memory


class MemoryStore:

    def __init__(self):
        self.memories = []

    def create(
        self,
        event,
        emotion="neutral",
        importance=1,
        category="general",
        tags=None,
    ):

        if tags is None:
            tags = []

        memory = Memory(
            event=event,
            emotion=emotion,
            importance=importance,
            category=category,
            tags=tags,
        )

        self.memories.append(memory)

        return memory

    def recall(self, query):

        query = query.lower()

        results = []

        for memory in self.memories:

            if query in memory.event.lower():
                memory.recalled += 1
                results.append(memory)

        return results

    def all(self):
        return self.memories

    def count(self):
        return len(self.memories)