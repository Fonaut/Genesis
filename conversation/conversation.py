from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Conversation:
    speaker: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)