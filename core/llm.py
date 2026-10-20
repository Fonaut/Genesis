import json
from dotenv import load_dotenv
from openai import OpenAI
from core.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS

load_dotenv()

client = OpenAI()


class LLM:

    def __init__(self):
        self.model = "gpt-4o-mini"

    def ask(self, prompt, max_tokens: int = 800, return_details: bool = False):
        """Standard response generation (no tools)."""
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        content = choice.message.content.strip() if choice.message.content else ""
        finish_reason = choice.finish_reason

        usage_info = {}
        if hasattr(response, "usage") and response.usage:
            usage_info = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }

        if return_details:
            return content, {
                "finish_reason": finish_reason,
                "is_truncated": (finish_reason == "length"),
                "usage": usage_info,
                "max_tokens": max_tokens,
            }
        return content

    def ask_with_tools(self, prompt, max_tokens: int = 800, return_details: bool = False):
        """Response generation with tool-calling support."""
        messages = [{"role": "user", "content": prompt}]

        cumulative_prompt_tokens = 0
        cumulative_completion_tokens = 0
        cumulative_total_tokens = 0
        finish_reason = None

        while True:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            msg = choice.message
            finish_reason = choice.finish_reason

            if hasattr(response, "usage") and response.usage:
                cumulative_prompt_tokens += getattr(response.usage, "prompt_tokens", 0)
                cumulative_completion_tokens += getattr(response.usage, "completion_tokens", 0)
                cumulative_total_tokens += getattr(response.usage, "total_tokens", 0)

            # No tool calls? Return the final answer.
            if not msg.tool_calls:
                content = msg.content.strip() if msg.content else ""
                if return_details:
                    return content, {
                        "finish_reason": finish_reason,
                        "is_truncated": (finish_reason == "length"),
                        "usage": {
                            "prompt_tokens": cumulative_prompt_tokens,
                            "completion_tokens": cumulative_completion_tokens,
                            "total_tokens": cumulative_total_tokens,
                        },
                        "max_tokens": max_tokens,
                    }
                return content

            # Otherwise, execute each requested tool
            messages.append(msg)

            for call in msg.tool_calls:
                fn_name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                print(f"🔧 [tool call: {fn_name}({args})]")

                fn = TOOL_FUNCTIONS.get(fn_name)
                result = fn(**args) if fn else f"Unknown tool: {fn_name}"
                print(f"   → {result}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })

    def think(self, user_message, memories, conversation):
        """Internal reasoning before responding."""
        memory_block = "\n".join(f"- {m}" for m in memories) if memories else "(none)"

        prompt = f"""
You are the internal reasoning system for Genesis, an AI companion.

Your job is NOT to respond to the user. Your job is to think privately
about what the user just said, using their memories and recent conversation.

User's message:
{user_message}

Relevant memories about the user:
{memory_block}

Recent conversation:
{conversation}

Write a brief internal thought (2-4 sentences max) covering:
1. What is the user actually asking or expressing?
2. Which memories (if any) are relevant?
3. What is the best way to respond?

Output ONLY the internal thought. No preamble, no labels.
"""

        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        return response.choices[0].message.content.strip()

    def extract_memory(self, conversation):
        """Extract stable, long-term traits about the user."""
        prompt = f"""
You are the long-term memory system for an AI individual.

Your goal is NOT to remember trivia.

Do NOT remember:
- favorite colors
- favorite foods
- hobbies
- temporary interests
- one-off preferences
- small facts
- questions the user asked

ONLY remember information that describes stable aspects of who the user is.

Examples

GOOD
Persistent
Curious
Reflective
Values honesty
Enjoys solving difficult problems
Learns by experimentation
Often thinks long term
Prefers deep conversations
Highly disciplined
Emotionally resilient
Strong desire for self-improvement

BAD
Favorite color is blue.
Favorite food is plantains.
Likes pizza.
Asked about goals.
Owns a dog.

If nothing about the person's character was learned return exactly:

NONE

Conversation:

{conversation}
"""

        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
        )
        return response.choices[0].message.content.strip()