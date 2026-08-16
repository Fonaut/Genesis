from conversation.conversation import Conversation


class ConversationHistory:
    def __init__(self):
        self.messages = []

    def add(self, speaker, message):
        ...

    def recent(self, limit=10):
        ...

    def clear(self):
        ...