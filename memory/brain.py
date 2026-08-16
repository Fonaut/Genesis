from memory.memory import MemoryStore
from conversation.history import ConversationHistory
from core.prompt_builder import PromptBuilder
from core.llm import LLM


class Brain:

    def __init__(self):
        self.memory = MemoryStore()
        self.history = ConversationHistory()
        self.prompt_builder = PromptBuilder()
        self.llm = LLM()
        self.personality = (
            "You are Genesis. "
            "You are thoughtful, curious, helpful, and remember previous conversations."
        )

    def process(self, speaker, message):

        # Add the user's message to the conversation history
        self.history.add(speaker, message)

        # Recall relevant memories
        related_memories = self.memory.recall(message)

        # Build the prompt
        prompt = self.prompt_builder.build(
            personality=self.personality,
            memories=related_memories,
            conversation=self.history.recent(),
            user_message=message,
        )

        # Generate a response (currently a stub)
        response = self.llm.generate(prompt)

        # Add Genesis's response to the conversation history
        self.history.add("Genesis", response)

        # Store the user's message as a memory
        memory = self.memory.create(message)

        # Debug output
        print("\nNew memory created")
        print("=" * 35)
        print(f"Memory ID : {memory.id}")
        print(f"Event     : {memory.event}")
        print(f"Emotion   : {memory.emotion}")
        print(f"Importance: {memory.importance}")
        print(f"Category  : {memory.category}")
        print(f"Created   : {memory.created}")
        print("=" * 35)

        return response