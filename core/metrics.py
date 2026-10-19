import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class LatencyTracker:
    """
    High-precision performance instrumentation tracker for Genesis.
    
    Measures duration of individual pipeline stages and records time-to-playback-start.
    Guarantees strict privacy: zero user text, reasoning, memories, or API keys are ever stored.
    Fail-safe: errors during logging will not crash or disrupt Genesis's operation.
    """

    def __init__(self, log_path: str = "logs/latency.jsonl"):
        self.log_path = Path(log_path)
        self.turn_start_time: Optional[float] = None
        self.playback_start_time: Optional[float] = None
        self.stages: Dict[str, float] = {}

    def start_turn(self) -> None:
        """Marks the start of a conversation turn (immediately after user recording ends)."""
        self.turn_start_time = time.perf_counter()
        self.playback_start_time = None
        self.stages = {}

    @contextmanager
    def measure(self, stage_name: str):
        """Context manager to measure the execution time of a specific pipeline stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            # Accumulate if stage is called multiple times in a turn
            self.stages[stage_name] = self.stages.get(stage_name, 0.0) + elapsed

    def mark_playback_start(self) -> None:
        """
        Marks the exact timestamp when the audio playback command is launched.
        Note: This measures time to playback start, not acoustic onset in room.
        """
        self.playback_start_time = time.perf_counter()

    def end_turn(self, flags: Optional[Dict[str, bool]] = None) -> Optional[Dict[str, Any]]:
        """
        Finalizes turn timing metrics, appends a record to the log file,
        and prints a compact performance summary to the console.
        """
        if self.turn_start_time is None:
            return None

        total_turn = time.perf_counter() - self.turn_start_time

        # Calculate time to playback start if marked, otherwise estimate from available stages
        time_to_playback_start = (
            self.playback_start_time - self.turn_start_time
            if self.playback_start_time is not None
            else None
        )

        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages_seconds": {k: round(v, 4) for k, v in self.stages.items()},
            "time_to_playback_start_seconds": round(time_to_playback_start, 4) if time_to_playback_start is not None else None,
            "total_turn_seconds": round(total_turn, 4),
            "flags": flags or {},
        }

        # Print clean, human-readable summary
        self._print_summary(record)

        # Fail-safe log write
        self._write_record(record)

        # Reset turn state
        self.turn_start_time = None
        self.playback_start_time = None
        self.stages = {}

        return record

    def _write_record(self, record: Dict[str, Any]) -> None:
        """Appends metric record to log file safely without risking main loop crash."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            # Resilient fallback: log warning but never crash conversation
            print(f"⚠️ [metrics] Failed to write latency log: {e}")

    def _print_summary(self, record: Dict[str, Any]) -> None:
        """Prints a compact latency breakdown to console."""
        stages = record.get("stages_seconds", {})
        tt_play = record.get("time_to_playback_start_seconds")
        total = record.get("total_turn_seconds")

        parts = [f"{k}: {v:.2f}s" for k, v in stages.items()]
        stages_str = " | ".join(parts) if parts else "none"

        tt_play_str = f"{tt_play:.2f}s" if tt_play is not None else "n/a"
        total_str = f"{total:.2f}s" if total is not None else "n/a"

        print(f"\n⏱️  [Latency Profile] Playback Start: {tt_play_str} | Total Turn: {total_str}")
        print(f"   Stages: {stages_str}\n")
