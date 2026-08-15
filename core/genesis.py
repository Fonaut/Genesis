import sys
from pathlib import Path

# Tell Python where the Genesis project is
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from memory.memory import Memory


class Genesis:

    def __init__(self):
        self.name = "Genesis"

    def first_memory(self):
        memory = Memory(
            event="I met my creator.",
            emotion="Curiosity",
            importance=40,
            created=datetime.now()
        )

        memory.describe()


mind = Genesis()
mind.first_memory()