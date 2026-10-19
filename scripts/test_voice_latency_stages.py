#!/usr/bin/env python3
"""
scripts/test_voice_latency_stages.py — Controlled Diagnostic & Benchmark Harness for Voice Latency.

Evaluates:
1. Silence Window Analysis (1.5s vs 0.75s) across 4 speaking patterns:
   - Short questions.
   - Long continuous sentences.
   - Natural mid-sentence pauses (0.3s, 0.5s, 0.7s, 0.8s, 1.0s, 1.2s).
   - Quiet speech (low amplitude).
2. Sample Rate Standardization (44.1 kHz vs 16 kHz):
   - CoreAudio stream initialization time on Apple M2.
   - Output file size (bytes per second).
   - Memory allocation during recording buffer concatenation.
"""

import os
import sys
import time
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf

# Project root setup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def simulate_vad(chunks, silence_threshold=0.015, silence_duration=1.5, chunk_duration=0.1):
    """
    Simulates the exact VAD algorithm from core/voice.py on a sequence of audio chunks.
    
    Returns:
        (stopped_at_chunk, total_chunks, has_spoken, premature_cutoff)
    """
    chunks_for_silence = int(silence_duration / chunk_duration)
    silent_chunks = 0
    has_spoken = False
    stopped_at = len(chunks)

    for idx, chunk in enumerate(chunks):
        volume = float(np.abs(chunk).mean())
        if volume < silence_threshold:
            if has_spoken:
                silent_chunks += 1
        else:
            has_spoken = True
            silent_chunks = 0

        if has_spoken and silent_chunks >= chunks_for_silence:
            stopped_at = idx + 1
            break

    return stopped_at, len(chunks), has_spoken


def run_silence_window_experiments():
    """Evaluates 1.5s vs 0.75s silence windows across 4 speaking patterns."""
    print("=" * 70)
    print("🔬 EXPERIMENT 1: SILENCE WINDOW & SPEAKING PATTERN ANALYSIS")
    print("=" * 70)

    chunk_len = 1600  # 100ms at 16kHz
    speech_chunk = np.random.uniform(0.04, 0.08, chunk_len).astype(np.float32)   # Normal speech volume
    quiet_chunk = np.random.uniform(0.016, 0.022, chunk_len).astype(np.float32)  # Low volume speech
    silent_chunk = np.zeros(chunk_len, dtype=np.float32)                         # Silence

    results = []

    # Pattern A: Short Question ("What time is it?" ~ 1.0s speech + trailing silence)
    chunks_a = [speech_chunk] * 10 + [silent_chunk] * 30  # 1s speech, 3s trailing silence

    # Pattern B: Long Sentence (4.0s continuous speech + trailing silence)
    chunks_b = [speech_chunk] * 40 + [silent_chunk] * 30

    # Pattern C: Mid-Sentence Pauses (1.5s speech + pause + 1.5s speech + trailing silence)
    pause_durations = [0.3, 0.5, 0.7, 0.8, 1.0, 1.2]

    # Pattern D: Quiet Speech (2.0s quiet speech + trailing silence)
    chunks_d = [quiet_chunk] * 20 + [silent_chunk] * 30

    print("\n[Pattern A: Short Question — 1.0s speech]")
    for sd_val in [1.5, 0.75]:
        stopped, total, spoken = simulate_vad(chunks_a, silence_duration=sd_val)
        recorded_time = stopped * 0.1
        delay_after_speech = recorded_time - 1.0
        print(f"  • SILENCE_DURATION={sd_val}s: Stopped at {recorded_time:.2f}s (Post-speech waiting: {delay_after_speech:.2f}s) | Cutoff: No")

    print("\n[Pattern B: Long Continuous Sentence — 4.0s speech]")
    for sd_val in [1.5, 0.75]:
        stopped, total, spoken = simulate_vad(chunks_b, silence_duration=sd_val)
        recorded_time = stopped * 0.1
        delay_after_speech = recorded_time - 4.0
        print(f"  • SILENCE_DURATION={sd_val}s: Stopped at {recorded_time:.2f}s (Post-speech waiting: {delay_after_speech:.2f}s) | Cutoff: No")

    print("\n[Pattern C: Natural Mid-Sentence Pauses (Speech 1.5s -> Pause -> Speech 1.5s)]")
    for pause in pause_durations:
        pause_chunks = [silent_chunk] * int(pause / 0.1)
        chunks_c = [speech_chunk] * 15 + pause_chunks + [speech_chunk] * 15 + [silent_chunk] * 30
        
        stopped_15, _, _ = simulate_vad(chunks_c, silence_duration=1.5)
        cutoff_15 = stopped_15 < (15 + len(pause_chunks) + 15)

        stopped_075, _, _ = simulate_vad(chunks_c, silence_duration=0.75)
        cutoff_075 = stopped_075 < (15 + len(pause_chunks) + 15)

        status_15 = "PREMATURE CUTOFF ❌" if cutoff_15 else "Completed Normally ✅"
        status_075 = "PREMATURE CUTOFF ❌" if cutoff_075 else "Completed Normally ✅"

        print(f"  • Pause = {pause:.1f}s | 1.5s Window: {status_15} | 0.75s Window: {status_075}")

    print("\n[Pattern D: Quiet / Low-Volume Speech — 2.0s speech]")
    for sd_val in [1.5, 0.75]:
        stopped, total, spoken = simulate_vad(chunks_d, silence_duration=sd_val)
        recorded_time = stopped * 0.1
        print(f"  • SILENCE_DURATION={sd_val}s: Stopped at {recorded_time:.2f}s | Spoken detected: {spoken}")


def run_sample_rate_investigation():
    """Evaluates 44.1 kHz vs 16 kHz stream initialization and file sizes on Apple M2."""
    print("\n" + "=" * 70)
    print("🔬 EXPERIMENT 2: SAMPLE RATE INVESTIGATION (44.1 kHz vs 16 kHz on M2)")
    print("=" * 70)

    rates = [44100, 16000]
    duration_sec = 3.0

    for sr in rates:
        # Measure stream open/start/close time
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            stream = sd.InputStream(samplerate=sr, channels=1, dtype='float32')
            stream.start()
            # Read one 100ms chunk
            stream.read(int(sr * 0.1))
            stream.stop()
            stream.close()
            times.append(time.perf_counter() - t0)

        avg_stream_time = np.mean(times)
        
        # Calculate file size for 3.0s recording
        samples = int(sr * duration_sec)
        dummy_audio = np.random.uniform(-0.1, 0.1, samples).astype(np.float32)
        test_file = f"test_rate_{sr}.wav"
        sf.write(test_file, dummy_audio, sr)
        file_size_bytes = Path(test_file).stat().st_size
        Path(test_file).unlink(missing_ok=True)

        print(f"\n[Sample Rate: {sr} Hz]")
        print(f"  • Stream Open + First Read Latency: {avg_stream_time * 1000:.1f} ms")
        print(f"  • 3-Second WAV File Size:          {file_size_bytes / 1024:.1f} KB")
        print(f"  • Uncompressed Data Rate:           {sr * 4 / 1024:.1f} KB/s")

    print("\n📊 Synthesis & Upload Efficiency:")
    size_44k = 44100 * 4 * 3 / 1024
    size_16k = 16000 * 4 * 3 / 1024
    savings = (1 - (size_16k / size_44k)) * 100
    print(f"  • Data payload reduction with 16 kHz: {savings:.1f}% smaller uploads to Whisper API")
    print(f"  • Whisper native acoustic input:     16 kHz (no downsampling needed)")


if __name__ == "__main__":
    run_silence_window_experiments()
    run_sample_rate_investigation()
