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


def wait_for_wake_word():
    """Listens for speech, transcribes it, returns when 'Genesis' is detected."""
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
            text = result.text.lower().strip()
            print(f"👂 Heard: '{result.text}'")

            if WAKE_WORD in text:
                print("✅ Wake word detected!")
                return
        except Exception as e:
            print(f"⚠️ Wake word check failed: {e}")