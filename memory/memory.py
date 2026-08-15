from dataclasses import dataclass
from datetime import datetime


@dataclass
class Memory:
    event: str
    emotion: str
    importance: int
    created: datetime
    recalls: int = 0

    def remember(self):
        self.recalls += 1

        if self.importance < 100:
            self.importance += 1

    def is_core(self):
        return self.importance >= 80

    def describe(self):
        print("----- Memory -----")
        print(f"Event: {self.event}")
        print(f"Emotion: {self.emotion}")
        print(f"Importance: {self.importance}")
        print(f"Times Recalled: {self.recalls}")
        print(f"Core Memory: {self.is_core()}")
        