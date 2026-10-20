"""
Genesis Development Journal — Data Models
Strictly isolated from Genesis's personal personality and memory.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any


def format_duration(seconds: float) -> str:
    """Formats a duration in seconds into a human-readable 'Xh Ym Zs' string."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m {secs:02d}s"
    elif mins > 0:
        return f"{mins}m {secs:02d}s"
    else:
        return f"{secs}s"


@dataclass
class BreakInterval:
    """Represents a discrete break interval during a development session."""
    break_id: str
    start_time: str          # ISO-8601 UTC
    end_time: Optional[str] = None  # ISO-8601 UTC
    reason: str = ""
    duration_sec: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.end_time is not None

    def close(self, end_dt: Optional[datetime] = None) -> float:
        """Closes the break interval and computes its duration in seconds."""
        if self.is_closed:
            return self.duration_sec
        end = end_dt or datetime.now(timezone.utc)
        self.end_time = end.isoformat()
        t_start = datetime.fromisoformat(self.start_time)
        self.duration_sec = max(0.0, (end - t_start).total_seconds())
        return self.duration_sec

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BreakInterval":
        return cls(**data)


@dataclass
class DevelopmentSession:
    """
    Represents a full development session, including activities, milestones,
    breaks, and calculated net active development duration.
    """
    session_id: str          # Format: YYYYMMDD_HHMMSS
    start_time: str          # ISO-8601 UTC
    end_time: Optional[str] = None  # ISO-8601 UTC
    status: str = "ACTIVE"   # "ACTIVE", "PAUSED", "COMPLETED", "RECONCILED", "DISCARDED"
    activity: str = ""
    milestone: str = ""
    summary: str = ""
    git_commits: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    breaks: List[BreakInterval] = field(default_factory=list)
    total_elapsed_sec: float = 0.0
    total_break_sec: float = 0.0
    active_work_sec: float = 0.0
    reconciled: bool = False
    reconciliation_note: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def is_paused(self) -> bool:
        return self.status == "PAUSED"

    @property
    def is_closed(self) -> bool:
        return self.status in ("COMPLETED", "RECONCILED", "DISCARDED")

    def current_break(self) -> Optional[BreakInterval]:
        """Returns the currently open break interval if paused."""
        if self.breaks and not self.breaks[-1].is_closed:
            return self.breaks[-1]
        return None

    def compute_durations(self, current_dt: Optional[datetime] = None) -> Dict[str, float]:
        """
        Computes total elapsed, break, and active work durations.
        If session is completed, uses end_time; otherwise evaluates live up to current_dt.
        """
        now = current_dt or datetime.now(timezone.utc)
        t_start = datetime.fromisoformat(self.start_time)
        t_end = datetime.fromisoformat(self.end_time) if self.end_time else now

        total_elapsed = max(0.0, (t_end - t_start).total_seconds())

        total_breaks = 0.0
        for b in self.breaks:
            if b.is_closed:
                total_breaks += b.duration_sec
            else:
                b_start = datetime.fromisoformat(b.start_time)
                total_breaks += max(0.0, (t_end - b_start).total_seconds())

        active_work = max(0.0, total_elapsed - total_breaks)
        return {
            "total_elapsed_sec": round(total_elapsed, 2),
            "total_break_sec": round(total_breaks, 2),
            "active_work_sec": round(active_work, 2),
        }

    def close(
        self,
        end_dt: Optional[datetime] = None,
        summary: str = "",
        git_commit: Optional[str] = None,
    ) -> "DevelopmentSession":
        """Closes the session, finalizing open breaks and calculating active work."""
        end = end_dt or datetime.now(timezone.utc)
        # If currently paused, close the open break first
        if self.is_paused and self.breaks and not self.breaks[-1].is_closed:
            self.breaks[-1].close(end)

        self.end_time = end.isoformat()
        self.status = "COMPLETED"
        if summary:
            self.summary = summary
        if git_commit and git_commit not in self.git_commits:
            self.git_commits.append(git_commit)

        durations = self.compute_durations(end)
        self.total_elapsed_sec = durations["total_elapsed_sec"]
        self.total_break_sec = durations["total_break_sec"]
        self.active_work_sec = durations["active_work_sec"]
        return self

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["breaks"] = [b.to_dict() for b in self.breaks]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DevelopmentSession":
        breaks_data = data.pop("breaks", [])
        breaks = [BreakInterval.from_dict(b) for b in breaks_data]
        return cls(breaks=breaks, **data)
