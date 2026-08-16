from memory.brain import Brain

brain = Brain()

print("Genesis is awake.")
print("Type 'exit' to quit.\n")

while True:
    message = input("You: ")

    if message.lower() == "exit":
        print("Genesis: Goodbye.")
        break

    response = brain.process("User", message)

    print("Genesis:", response)