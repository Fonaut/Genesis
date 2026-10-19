from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()


class Planner:

    def __init__(self):
        self.model = "gpt-4o-mini"  # <-- FIXED HERE

    def decide(self, user):

        prompt = f"""
You are the planning system for an AI assistant.

Your job is to decide what actions should happen before responding.

Possible actions:

SEARCH_MEMORY
SAVE_MEMORY
NONE

Rules:

- SEARCH_MEMORY if previous knowledge about the user would help answer.
- SAVE_MEMORY if the user reveals something stable about themselves.
- Return BOTH if both apply.
- Return NONE if neither is needed.

Examples:

User:
I like pizza.

SEARCH_MEMORY
SAVE_MEMORY

User:
What's my favorite food?

SEARCH_MEMORY

User:
Hello

NONE

User:
I'm trying to become a software engineer.

SEARCH_MEMORY
SAVE_MEMORY

User:
{user}
"""

        response = client.responses.create(
            model=self.model,
            input=prompt,
            max_output_tokens=20,
        )

        text = response.output_text.upper()

        return {
            "search_memory": "SEARCH_MEMORY" in text,
            "save_memory": "SAVE_MEMORY" in text,
        }