import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.metrics import LatencyTracker


class TestLatencyTracker(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_file = Path(self.temp_dir.name) / "test_latency.jsonl"
        self.tracker = LatencyTracker(log_path=str(self.log_file))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_measure_single_stage(self):
        """Verifies that individual stage measurement records elapsed time."""
        # Simulate time.perf_counter increments
        perf_times = [0.0, 1.0, 2.5]
        with patch("time.perf_counter", side_effect=perf_times):
            self.tracker.start_turn()
            with self.tracker.measure("planner_decision"):
                pass  # simulates stage execution (1.0 to 2.5 = 1.5s)

        self.assertAlmostEqual(self.tracker.stages["planner_decision"], 1.5, places=3)

    def test_time_to_playback_start(self):
        """Verifies time_to_playback_start is calculated accurately from start_turn."""
        perf_times = [
            10.0,  # start_turn
            10.2,  # start transcription
            11.0,  # end transcription (0.8s)
            11.1,  # start synthesis
            11.8,  # end synthesis (0.7s)
            12.5,  # mark_playback_start (elapsed = 12.5 - 10.0 = 2.5s)
            13.0,  # start playback
            15.0,  # end playback
            15.5,  # end_turn (total = 15.5 - 10.0 = 5.5s)
        ]

        with patch("time.perf_counter", side_effect=perf_times):
            self.tracker.start_turn()
            with self.tracker.measure("speech_transcription"):
                pass
            with self.tracker.measure("speech_synthesis"):
                pass
            self.tracker.mark_playback_start()
            with self.tracker.measure("audio_playback"):
                pass
            record = self.tracker.end_turn()

        self.assertIsNotNone(record)
        self.assertAlmostEqual(record["time_to_playback_start_seconds"], 2.5, places=3)
        self.assertAlmostEqual(record["total_turn_seconds"], 5.5, places=3)

    def test_all_eight_stages_tracked(self):
        """Verifies all 8 conversation stages are measured and present in record."""
        stages = [
            "speech_transcription",
            "planner_decision",
            "memory_recall",
            "reasoning",
            "response_generation",
            "speech_synthesis",
            "audio_playback",
            "memory_extraction",
        ]

        # Provide a monotonic time generator for enter/exit of each stage
        current_time = 0.0
        def fake_perf():
            nonlocal current_time
            current_time += 0.1
            return current_time

        with patch("time.perf_counter", side_effect=fake_perf):
            self.tracker.start_turn()
            for stage in stages:
                with self.tracker.measure(stage):
                    pass
            self.tracker.mark_playback_start()
            record = self.tracker.end_turn(flags={"memory_searched": True, "memory_saved": False})

        self.assertIsNotNone(record)
        for stage in stages:
            self.assertIn(stage, record["stages_seconds"])
            self.assertGreater(record["stages_seconds"][stage], 0.0)

        self.assertTrue(record["flags"]["memory_searched"])
        self.assertFalse(record["flags"]["memory_saved"])

    def test_strict_privacy_zero_sensitive_data(self):
        """Ensures that logged JSON records never contain text, thoughts, or credentials."""
        self.tracker.start_turn()
        with self.tracker.measure("speech_transcription"):
            pass
        self.tracker.mark_playback_start()
        record = self.tracker.end_turn(flags={"memory_searched": True, "memory_saved": True})

        # Check raw json written to disk
        self.assertTrue(self.log_file.exists())
        log_content = self.log_file.read_text(encoding="utf-8")
        parsed = json.loads(log_content.strip())

        forbidden_keys = [
            "text", "user", "assistant", "prompt", "thought", "message",
            "memory", "api_key", "token", "secret", "transcript", "content"
        ]

        # Verify no forbidden keys at top level or stages
        for key in forbidden_keys:
            self.assertNotIn(key, parsed)
            self.assertNotIn(key, parsed.get("stages_seconds", {}))

        # Values should strictly be floats, ints, bools, or ISO strings
        self.assertIsInstance(parsed["timestamp"], str)
        self.assertIsInstance(parsed["stages_seconds"], dict)
        self.assertIsInstance(parsed["total_turn_seconds"], float)

    def test_logging_resilience_on_write_failure(self):
        """Verifies Requirement 5: Genesis does not crash if log file writing fails."""
        self.tracker.start_turn()
        with self.tracker.measure("speech_transcription"):
            pass

        # Simulate write failure (e.g., read-only filesystem or OS permission error)
        with patch("builtins.open", side_effect=PermissionError("Simulated permission denied")):
            # end_turn must NOT raise an exception
            record = self.tracker.end_turn()

        # The in-memory record is still returned cleanly
        self.assertIsNotNone(record)
        self.assertIn("speech_transcription", record["stages_seconds"])


if __name__ == "__main__":
    unittest.main()
