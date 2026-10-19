from core.llm import LLM
from core.planner import Planner
from conversation.history import History
from memory.brain import Brain
from identity.identity import Identity
from core.voice import record_audio, transcribe_audio, speak, synthesize_speech, play_audio
from core.wake_word import wait_for_wake_word, parse_wake_word
from core.metrics import LatencyTracker
from core.config import SHOW_INTERNAL_THOUGHTS

# Initialize all components
ai = LLM()
planner = Planner()
history = History()
brain = Brain()
identity = Identity()
tracker = LatencyTracker()

# First-time introduction
if not identity.load().get("introduced", False):
    intro_text = identity.prompt()
    print(intro_text)
    speak(intro_text)
    identity.introduced()

while True:
    # 1. Wait for "Genesis" to be said (supports one-shot commands like "Genesis, what time is it?")
    command = wait_for_wake_word()

    if command:
        # One-shot command spoken in the same breath
        user = command
        tracker.start_turn()
        tracker.stages["speech_transcription"] = 0.0
    else:
        # Two-stage mode: wake word alone was detected, now record the actual command
        audio_file = record_audio()
        tracker.start_turn()

        with tracker.measure("speech_transcription"):
            user = transcribe_audio(audio_file)

        # If user repeated the wake word at the start of their command, strip only the activation phrase
        is_wake, trailing = parse_wake_word(user)
        if is_wake:
            user = trailing

        # Handle empty user input or saying "Genesis" alone without a query
        if not user.strip():
            print("🤷 Didn't catch that. Going back to sleep.")
            continue

    print(f"\nYou: {user}")
    history.add("user", user)

    conversation = history.recent()

    with tracker.measure("planner_decision"):
        plan = planner.decide(user)

    with tracker.measure("memory_recall"):
        if plan["search_memory"]:
            memories = brain.recall(user)
        else:
            memories = []

    memory_text = "\n".join(f"- {memory}" for memory in memories)

    # 3. Reasoning layer
    with tracker.measure("reasoning"):
        thought = ai.think(user, memories, conversation)

    if SHOW_INTERNAL_THOUGHTS:
        print(f"\n💭 [internal thought: {thought}]\n")

    prompt = f"""
{identity.prompt()}

Relevant long-term memories:

{memory_text}

Recent conversation:

{conversation}

Internal thought (your private reasoning — do not mention this explicitly):
{thought}

User:
{user}

Instructions:
- Respond naturally.
- Keep answers concise unless the user asks for detail.
- If you need information (time, files, apps), use the available tools.
"""

    # 4. Response with tool support
    with tracker.measure("response_generation"):
        answer = ai.ask_with_tools(prompt)

    print(f"\nGenesis: {answer}\n")

    with tracker.measure("speech_synthesis"):
        output_file = synthesize_speech(answer)

    tracker.mark_playback_start()
    print("🔊 Genesis is speaking...")

    with tracker.measure("audio_playback"):
        play_audio(output_file)

    history.add("assistant", answer)

    # 5. Save long-term memory only when appropriate
    with tracker.measure("memory_extraction"):
        if plan["save_memory"]:
            transcript = f"User:\n{user}\n\nGenesis:\n{answer}"
            memory = ai.extract_memory(transcript)
            if memory != "NONE":
                brain.remember(memory)

    tracker.end_turn(flags={
        "memory_searched": bool(plan["search_memory"]),
        "memory_saved": bool(plan["save_memory"]),
    })