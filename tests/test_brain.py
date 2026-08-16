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
    importance=8,
)

brain.remember(
    "Python 3.14 is installed.",
    emotion="excited",
    importance=9,
)

print("Memory count:", brain.count())
print()

print("All memories:")
for memory in brain.recall_all():
    memory.describe()

print()
print("Search results:")
for memory in brain.search("Python"):
    memory.describe()