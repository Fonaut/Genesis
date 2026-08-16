class PromptBuilder:
    def build(
        self,
        personality,
        memories,
        conversation,
        user_message,
    ):
        prompt = []

        # Personality
        prompt.append("=== Personality ===")
        prompt.append(personality)

        # Memories
        prompt.append("\n=== Relevant Memories ===")

        if memories:
            for memory in memories:
                prompt.append(f"- {memory.event}")
        else:
            prompt.append("None")

        # Conversation History
        prompt.append("\n=== Conversation ===")

        if conversation:
            for msg in conversation:
                prompt.append(f"{msg.speaker}: {msg.message}")

        # Current User Message
        prompt.append("\n=== User ===")
        prompt.append(user_message)

        return "\n".join(prompt)