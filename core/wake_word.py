import re
from typing import Tuple
import sounddevice as sd
import soundfile as sf
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI()

WAKE_WORD = "genesis"
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 0.0012   # Volume level below which = silence
MAX_CHUNK_SECONDS = 5       # Max length of a single listening window
SILENCE_CHUNKS_TO_STOP = 8  # 0.8 sec of silence ends the window


def parse_wake_word(transcript: str, wake_word: str = WAKE_WORD) -> Tuple[bool, str]:
    """
    Parses a transcript to detect whether an activation phrase was spoken.

    Returns:
        (is_detected, command)
        - is_detected: True if the wake word was recognized as an activation trigger.
        - command: Trailing text following the activation phrase (empty string if wake word alone).
    """
    if not transcript or not transcript.strip():
        return False, ""

    # Primary match: optional greeting ("hey", "hi", "hello", "ok") followed by the wake word,
    # bounded by word boundaries to avoid false substring matches like "general" or "regenesis".
    pattern = rf"^\s*(?:(?:hey|hi|hello|ok)\s+)?\b{re.escape(wake_word)}\b[\s,.:;!?-]*"
    match = re.search(pattern, transcript, flags=re.IGNORECASE)

    if match:
        # Strip only leading separators between wake word and command, preserving sentence punctuation at the end
        command = transcript[match.end():].lstrip(" ,.:;!?-").strip()
        return True, command

    # Secondary match: handle brief conversational fillers before the wake word (e.g., "Uh, Genesis, what time is it?")
    fallback_pattern = rf"\b{re.escape(wake_word)}\b[\s,.:;!?-]*"
    fallback_match = re.search(fallback_pattern, transcript, flags=re.IGNORECASE)
    if fallback_match:
        preceding = transcript[:fallback_match.start()].strip(" ,.!?-\t\n").lower()
        filler_words = {"uh", "um", "so", "well", "hey", "hi", "hello", "ok", "please"}
        if not preceding or preceding in filler_words:
            command = transcript[fallback_match.end():].lstrip(" ,.:;!?-").strip()
            return True, command

    return False, ""


def wait_for_wake_word() -> str:
    """
    Listens for speech, transcribes it, and checks for the wake word.

    Returns:
        The trailing command string if the user asked a question in the same breath (one-shot),
        or an empty string ("") if only the wake word was spoken (two-stage).
    """
    print("\n😴 Waiting for wake word... (say 'Genesis')")


    while True:
        chunk_size = int(SAMPLE_RATE * 0.1)  # 100ms chunks
        frames = []
        has_spoken = False
        silent_chunks = 0
        max_chunks = int(MAX_CHUNK_SECONDS / 0.1)

        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32')
        stream.start()

        try:
            for _ in range(max_chunks):
                chunk, _ = stream.read(chunk_size)
                volume = float(np.abs(chunk).mean())

                if volume > SILENCE_THRESHOLD:
                    has_spoken = True
                    silent_chunks = 0
                    frames.append(chunk.copy())
                elif has_spoken:
                    silent_chunks += 1
                    frames.append(chunk.copy())
                    if silent_chunks >= SILENCE_CHUNKS_TO_STOP:
                        break
                # else: still silent before speech started — skip
        finally:
            stream.stop()
            stream.close()

        # If nothing meaningful was captured, loop back and keep listening
        if not has_spoken or len(frames) < 3:
            continue

        # Save and send to Whisper
        recording = np.concatenate(frames, axis=0)
        sf.write("wake_check.wav", recording, SAMPLE_RATE)

        try:
            with open("wake_check.wav", "rb") as f:
                result = client.audio.transcriptions.create(model="whisper-1", file=f)
            raw_text = result.text.strip()
            print(f"👂 Heard: '{raw_text}'")

            is_wake, command = parse_wake_word(raw_text, WAKE_WORD)
            if is_wake:
                if command:
                    print(f"✅ Wake word detected with one-shot command: '{command}'")
                else:
                    print("✅ Wake word detected! (listening for command...)")
                return command
        except Exception as e:
            print(f"⚠️ Wake word check failed: {e}")