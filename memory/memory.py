from dataclasses import dataclass, field
from datetime import datetime
import uuid


@dataclass
class Memory:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    event: str = ""
    emotion: str = ""
    importance: int = 0
    created: datetime = field(default_factory=datetime.now)
    category: str = ""

    tags: list[str] = field(default_factory=list)
    reflection: str | None = None
    connections: list[str] = field(default_factory=list)

    times_recalled: int = 0

    def recall(self):
        self.times_recalled += 1

    def reflect(self, thought: str):
        self.reflection = thought

    def connect(self, other_memory):
        self.connections.append(other_memory.id)