import os
import numpy as np
import sounddevice as sd
import soundfile as sf
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI()

SAMPLE_RATE = 44100
CHANNELS = 1
INPUT_FILE = "input.wav"
OUTPUT_FILE = "output.mp3"

# Tuning knobs
SILENCE_THRESHOLD = 0.015   # how quiet = "silent" (raise if too sensitive)
SILENCE_DURATION = 1.5      # seconds of silence before stopping
MAX_DURATION = 30           # hard cutoff (safety net)


def record_audio():
    """Records until the user stops speaking (voice activity detection)."""
    chunk_size = int(SAMPLE_RATE * 0.1)  # 100ms chunks
    chunks_for_silence = int(SILENCE_DURATION / 0.1)
    max_chunks = int(MAX_DURATION / 0.1)

    print("\n🎤 Listening... (speak now, will stop when you pause)")
    frames = []
    silent_chunks = 0
    has_spoken = False

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
    stream.start()

    try:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_size)
            frames.append(chunk.copy())
            volume = float(np.abs(chunk).mean())

            if volume < SILENCE_THRESHOLD:
                if has_spoken:
                    silent_chunks += 1
            else:
                has_spoken = True
                silent_chunks = 0

            # Stop once we've had enough silence AFTER the user has spoken
            if has_spoken and silent_chunks >= chunks_for_silence:
                break
    finally:
        stream.stop()
        stream.close()

    recording = np.concatenate(frames, axis=0)
    sf.write(INPUT_FILE, recording, SAMPLE_RATE)
    print("✅ Recording finished.")
    return INPUT_FILE


def transcribe_audio(file_path):
    """Sends the audio file to OpenAI Whisper for transcription."""
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return transcript.text


def synthesize_speech(text, output_file=OUTPUT_FILE):
    """Synthesizes text to speech using OpenAI TTS without playing it."""
    response = client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text
    )
    response.stream_to_file(output_file)
    return output_file


def play_audio(file_path=OUTPUT_FILE):
    """Plays an audio file via afplay."""
    os.system(f"afplay {file_path}")


def speak(text):
    """Converts text to speech using OpenAI TTS and plays it."""
    print("🔊 Genesis is speaking...")
    audio_file = synthesize_speech(text)
    play_audio(audio_file)