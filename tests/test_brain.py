from memory.brain import Brain


brain = Brain()

brain.remember(
    "Fonaut created me.",
    emotion="happy",
    importance=10,
)

brain.remember(
    "I learned about dataclasses.",
    emotion="curious",
)

print("Memory count:", brain.count())

for memory in brain.recall_all():
    print(memory)