from core.llm import LLM
from core.planner import Planner
from conversation.history import History
from memory.brain import Brain
from identity.identity import Identity
from core.voice import record_audio, transcribe_audio, speak, synthesize_speech, play_audio
from core.wake_word import wait_for_wake_word
from core.metrics import LatencyTracker

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
    # 1. Wait for "Genesis" to be said
    wait_for_wake_word()

    # 2. Record the actual command
    audio_file = record_audio()
    tracker.start_turn()

    with tracker.measure("speech_transcription"):
        user = transcribe_audio(audio_file)

    # Sometimes the user says nothing after waking — handle that
    if not user.strip():
        print("🤷 Didn't catch that. Going back to sleep.")
        continue

    # Strip out the wake word itself if Whisper kept it in the transcript
    cleaned = user.lower().replace("genesis", "").strip(" ,.!?-")
    if cleaned:
        user = cleaned

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