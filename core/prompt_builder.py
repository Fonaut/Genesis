from pathlib import Path

PROMPT_PATH = Path("personality/system_prompt.txt")


def build_prompt(memories, history, user_message):
    system_prompt = PROMPT_PATH.read_text()

    prompt = f"""
{system_prompt}

Long-Term Memory:
{memories}

Recent Conversation:
{history}

Current User Message:
{user_message}
"""

    return prompt