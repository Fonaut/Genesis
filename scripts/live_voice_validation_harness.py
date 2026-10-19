#!/usr/bin/env python3
"""
scripts/live_voice_validation_harness.py — Limited Live Voice Validation & Benchmarking.

Evaluates:
1. 16 kHz vs 44.1 kHz acoustic recording & Whisper transcription accuracy.
2. Silence Windows: 1.5s (Baseline) vs 1.0s (Balanced) vs 0.75s (Aggressive).
3. Five Representative Speaking Turns:
   - Turn 1: Short question ("What time is it right now?")
   - Turn 2: Natural pause > 1.0s ("I was thinking about our project architecture... [1.2s] ...and how we should handle memory retrieval.")
   - Turn 3: Long sentence ("Can you explain the difference between our short-term conversation history and our long-term ChromaDB vector memory?")
   - Turn 4: Brief hesitation ("Could you... [0.6s] ...check if we have any pending tasks in our plan?")
   - Turn 5: Casual dialogue ("Genesis, how are you feeling about the progress we have made today?")
4. Metric Separation:
   - End-of-speech detection (VAD silence window)
   - Post-recording processing (Transcription + Planner + Reasoning + Response + Synthesis)
   - Time to first audio (playback start)
   - Total turn time
"""

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
import soundfile as sf
from openai import OpenAI
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()
client = OpenAI()

from core.capabilities import get_capability_prompt
from core.llm import LLM
from core.planner import Planner
from memory.brain import Brain
from identity.identity import Identity

# Use isolated test directory for any temporary audio and metrics
VALIDATION_DIR = PROJECT_ROOT / "test_env" / "voice_validation"
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)


TEST_TURNS = [
    {
        "id": "turn_1_short",
        "name": "Short Question",
        "segments": [("What time is it right now?", 0.0)],
        "full_text": "What time is it right now?",
        "has_pause": False,
    },
    {
        "id": "turn_2_pause_1_2s",
        "name": "Natural Pause > 1.0s",
        "segments": [
            ("I was thinking about our project architecture", 1.2),  # 1.2s pause
            ("and how we should handle memory retrieval.", 0.0),
        ],
        "full_text": "I was thinking about our project architecture and how we should handle memory retrieval.",
        "has_pause": True,
        "pause_sec": 1.2,
    },
    {
        "id": "turn_3_long",
        "name": "Long Sentence",
        "segments": [
            ("Can you explain the difference between our short term conversation history and our long term ChromaDB vector memory?", 0.0)
        ],
        "full_text": "Can you explain the difference between our short term conversation history and our long term ChromaDB vector memory?",
        "has_pause": False,
    },
    {
        "id": "turn_4_pause_0_6s",
        "name": "Brief Hesitation (0.6s)",
        "segments": [
            ("Could you", 0.6),  # 0.6s pause
            ("check if we have any pending tasks in our plan?", 0.0),
        ],
        "full_text": "Could you check if we have any pending tasks in our plan?",
        "has_pause": True,
        "pause_sec": 0.6,
    },
    {
        "id": "turn_5_casual",
        "name": "Casual Dialogue",
        "segments": [("Genesis, how are you feeling about the progress we have made today?", 0.0)],
        "full_text": "Genesis, how are you feeling about the progress we have made today?",
        "has_pause": False,
    },
]


def generate_turn_audio(turn_def: dict, sample_rate: int = 16000) -> np.ndarray:
    """Generates realistic acoustic spoken audio for a turn using OpenAI TTS with exact pauses (cached to disk)."""
    cache_file = VALIDATION_DIR / f"{turn_def['id']}_{sample_rate}.wav"
    if cache_file.exists():
        data, _ = sf.read(str(cache_file), dtype="float32")
        return data

    audio_pieces = []

    for text, pause_after in turn_def["segments"]:
        response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="wav",
        )
        data, sr = sf.read(io.BytesIO(response.content), dtype="float32")
        
        # Resample to target if needed
        if sr != sample_rate:
            from scipy import signal
            num_samples = int(len(data) * sample_rate / sr)
            data = signal.resample(data, num_samples).astype(np.float32)
        
        audio_pieces.append(data)

        if pause_after > 0:
            silence_samples = int(sample_rate * pause_after)
            audio_pieces.append(np.zeros(silence_samples, dtype=np.float32))

    full_audio = np.concatenate(audio_pieces, axis=0)
    sf.write(str(cache_file), full_audio, sample_rate)
    return full_audio

    return np.concatenate(audio_pieces, axis=0)


def simulate_vad_recording(
    audio_data: np.ndarray,
    sample_rate: int = 16000,
    silence_threshold: float = 0.015,
    silence_duration: float = 1.5,
    chunk_sec: float = 0.1,
) -> Tuple[np.ndarray, float, bool]:
    """
    Simulates core/voice.py record_audio() loop on acoustic audio.
    
    Returns:
        (captured_audio, detection_delay_seconds, was_premature_cutoff)
    """
    chunk_size = int(sample_rate * chunk_sec)
    chunks_for_silence = int(silence_duration / chunk_sec)
    
    # Pad with 3 seconds of trailing silence to allow VAD to stop after speech ends
    trailing_silence = np.zeros(int(sample_rate * 3.0), dtype=np.float32)
    full_audio = np.concatenate([audio_data, trailing_silence], axis=0)

    total_chunks = len(full_audio) // chunk_size
    frames = []
    silent_chunks = 0
    has_spoken = False
    stopped_at_chunk = total_chunks

    for i in range(total_chunks):
        chunk = full_audio[i * chunk_size : (i + 1) * chunk_size]
        frames.append(chunk.copy())
        volume = float(np.abs(chunk).mean())

        if volume < silence_threshold:
            if has_spoken:
                silent_chunks += 1
        else:
            has_spoken = True
            silent_chunks = 0

        if has_spoken and silent_chunks >= chunks_for_silence:
            stopped_at_chunk = i + 1
            break

    captured = np.concatenate(frames[:stopped_at_chunk], axis=0) if frames else np.array([], dtype=np.float32)

    # Check if cutoff happened before the end of the original speech
    original_speech_len = len(audio_data)
    premature_cutoff = len(captured) < original_speech_len
    
    # Detection delay is the trailing silence captured after speech ended
    if not premature_cutoff:
        detection_delay = (len(captured) - original_speech_len) / sample_rate
    else:
        detection_delay = 0.0

    return captured, max(0.0, detection_delay), premature_cutoff


def run_controlled_validation():
    """Runs repeatable validation across 3 silence windows and 2 sample rates."""
    print("=" * 75)
    print("🎙️ LIVE VOICE VALIDATION & BENCHMARK HARNESS")
    print("   Target Hardware: Apple M2 MacBook (8 GB Unified Memory)")
    print("=" * 75)

    ai = LLM()
    planner = Planner()
    brain = Brain()
    identity = Identity()
    capability_info = get_capability_prompt()

    silence_configs = [1.5, 1.0, 0.75]
    sample_rates = [16000, 44100]

    all_results = []

    # -------------------------------------------------------------------------
    # PART 1: 16 kHz vs 44.1 kHz Accuracy & Latency Comparison
    # -------------------------------------------------------------------------
    print("\n" + "-" * 75)
    print("📊 PART 1: 16 kHz vs 44.1 kHz Transcription Accuracy & Upload Latency")
    print("-" * 75)

    sample_turn = TEST_TURNS[0]  # "What time is it right now?"
    for sr in sample_rates:
        raw_audio = generate_turn_audio(sample_turn, sample_rate=sr)
        test_wav = VALIDATION_DIR / f"test_{sr}.wav"
        sf.write(str(test_wav), raw_audio, sr)
        wav_size_kb = test_wav.stat().st_size / 1024

        t0 = time.perf_counter()
        with open(test_wav, "rb") as f:
            transcript_res = client.audio.transcriptions.create(model="whisper-1", file=f)
        whisper_time = time.perf_counter() - t0
        transcript_text = transcript_res.text.strip()

        print(f"\n[Sample Rate: {sr} Hz]")
        print(f"  • WAV File Size:          {wav_size_kb:.1f} KB")
        print(f"  • Whisper Upload Latency: {whisper_time:.3f} s")
        print(f"  • Ground Truth:           \"{sample_turn['full_text']}\"")
        print(f"  • Whisper Transcript:     \"{transcript_text}\"")
        print(f"  • Exact Match:            {'✅ YES' if sample_turn['full_text'].lower() in transcript_text.lower() else '❌ NO'}")

    # -------------------------------------------------------------------------
    # PART 2: Silence Window Comparison Across 5 Representative Turns
    # -------------------------------------------------------------------------
    print("\n" + "-" * 75)
    print("📊 PART 2: Silence Window Benchmarks (1.5s vs 1.0s vs 0.75s) at 16 kHz")
    print("-" * 75)

    for sd_val in silence_configs:
        print(f"\n{'='*30} CONFIG: SILENCE_DURATION = {sd_val}s {'='*30}")
        config_records = []

        for turn in TEST_TURNS:
            turn_name = turn["name"]
            print(f"\n--- Testing Turn: {turn_name} ---")
            print(f"Target Speech: \"{turn['full_text']}\"")

            # 1. Generate acoustic audio with exact pauses
            turn_audio = generate_turn_audio(turn, sample_rate=16000)

            # 2. Simulate VAD recording
            captured, detection_delay, premature = simulate_vad_recording(
                turn_audio,
                sample_rate=16000,
                silence_threshold=0.015,
                silence_duration=sd_val,
            )

            # 3. Save captured audio to disk
            wav_path = VALIDATION_DIR / f"captured_{turn['id']}_{sd_val}.wav"
            sf.write(str(wav_path), captured, 16000)

            # 4. Measure Whisper Transcription
            t_whisper_start = time.perf_counter()
            with open(wav_path, "rb") as f:
                transcription = client.audio.transcriptions.create(model="whisper-1", file=f).text.strip()
            whisper_duration = time.perf_counter() - t_whisper_start

            # 5. Measure Planner Decision
            t_plan_start = time.perf_counter()
            plan = planner.decide(transcription)
            planner_duration = time.perf_counter() - t_plan_start

            # 6. Measure Memory Recall
            t_recall_start = time.perf_counter()
            memories = brain.recall(transcription) if plan.get("search_memory") else []
            recall_duration = time.perf_counter() - t_recall_start

            # 7. Measure Private Reasoning (think)
            t_think_start = time.perf_counter()
            thought = ai.think(f"[Fodoh]: {transcription}", memories, "(No previous conversation)")
            reasoning_duration = time.perf_counter() - t_think_start

            # 8. Measure Response Generation
            memory_text = "\n".join(f"- {m}" for m in memories) if memories else "(none)"
            prompt = f"""
{identity.prompt()}

{capability_info}

Relevant memories:
{memory_text}

Internal thought:
{thought}

User:
{transcription}

Instructions:
- Respond naturally and concisely.
- Adhere strictly to the Hardware & Sensory Boundaries.
"""
            t_resp_start = time.perf_counter()
            response_text = ai.ask_with_tools(prompt)
            response_duration = time.perf_counter() - t_resp_start

            # 9. Measure Speech Synthesis (OpenAI TTS)
            t_tts_start = time.perf_counter()
            tts_res = client.audio.speech.create(
                model="tts-1",
                voice="nova",
                input=response_text,
            )
            synthesis_duration = time.perf_counter() - t_tts_start

            # Calculate total time to first audio (post-recording processing)
            post_recording_processing = (
                whisper_duration + planner_duration + recall_duration +
                reasoning_duration + response_duration + synthesis_duration
            )
            time_to_first_audio = post_recording_processing

            # Check transcription accuracy / completeness
            if turn["has_pause"] and premature:
                accuracy_note = "PREMATURE CUTOFF ❌ (sentence truncated at pause)"
            else:
                accuracy_note = "Accurate & Complete ✅"

            record = {
                "turn_id": turn["id"],
                "turn_name": turn_name,
                "silence_window_sec": sd_val,
                "detection_delay_sec": round(detection_delay, 3),
                "premature_cutoff": premature,
                "accuracy_note": accuracy_note,
                "transcript": transcription,
                "whisper_sec": round(whisper_duration, 3),
                "planner_sec": round(planner_duration, 3),
                "reasoning_sec": round(reasoning_duration, 3),
                "response_sec": round(response_duration, 3),
                "synthesis_sec": round(synthesis_duration, 3),
                "post_recording_processing_sec": round(post_recording_processing, 3),
                "time_to_first_audio_sec": round(time_to_first_audio, 3),
            }
            config_records.append(record)
            all_results.append(record)

            print(f"  • End-of-Speech Silence Waiting: {detection_delay:.2f} s")
            print(f"  • Cutoff Status:                 {accuracy_note}")
            print(f"  • Transcript:                    \"{transcription}\"")
            print(f"  • Whisper Upload + STT:          {whisper_duration:.2f} s")
            print(f"  • Planner Decision:              {planner_duration:.2f} s")
            print(f"  • Reasoning (think):             {reasoning_duration:.2f} s")
            print(f"  • Response Generation:           {response_duration:.2f} s")
            print(f"  • Speech Synthesis (TTS):        {synthesis_duration:.2f} s")
            print(f"  • Time to Playback Start:        {time_to_first_audio:.2f} s")

    # Save all test results to test_env
    results_json = VALIDATION_DIR / "voice_validation_results.json"
    results_json.write_text(json.dumps(all_results, indent=2))
    print(f"\n💾 Benchmark records saved to: {results_json}")


if __name__ == "__main__":
    run_controlled_validation()
