from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class Memory:
    id: str = field(default_factory=lambda: str(uuid4()))
    event: str = ""
    emotion: str = "neutral"
    importance: int = 1
    category: str = "general"
    created: datetime = field(default_factory=datetime.now)

    tags: list[str] = field(default_factory=list)

    reflection: str | None = None

    links: list[str] = field(default_factory=list)

    recalled: int = 0