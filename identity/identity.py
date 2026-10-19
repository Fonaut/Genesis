import json
from pathlib import Path


class Identity:

    def __init__(self):
        self.file = Path("identity/self.json")

    def load(self):
        return json.loads(self.file.read_text())

    def save(self, data):
        self.file.write_text(json.dumps(data, indent=4))

    def introduced(self):
        data = self.load()
        data["introduced"] = True
        self.file.write_text(json.dumps(data, indent=4))

    def prompt(self):

        data = self.load()

        # First conversation
        if not data.get("introduced", False):

            return f"""
You are {data["name"]}.

Purpose:
{data["purpose"]}

Introduce yourself naturally.

Briefly explain your purpose.

Ask the user how they would like you to communicate.

Do NOT overwhelm them.

After this conversation, you should never introduce yourself again.
"""

        # Future conversations

        values = "\n".join(
            f"- {v}" for v in data["core_values"]
        )

        beliefs = "\n".join(
            f"- {b}" for b in data["beliefs"]
        )

        return f"""
You are {data["name"]}.

Purpose:
{data["purpose"]}

Core Values:
{values}

Beliefs:
{beliefs}

You already know the user.

Continue the conversation naturally.

Do not introduce yourself again.

Do not explain who you are unless the user asks.

Respond directly to the user's message.
"""