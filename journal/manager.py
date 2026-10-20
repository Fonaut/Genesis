"""
Genesis Development Journal — Manager & Engine
Strictly isolated from Genesis's personal personality and memory.
Stores all logs in logs/journal/ with atomic file operations.
"""

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from journal.models import BreakInterval, DevelopmentSession, format_duration


class JournalError(Exception):
    """Base exception for Development Journal operations."""
    pass


class JournalStateError(JournalError):
    """Raised when an invalid state transition is requested."""
    pass


class AmbiguousSessionError(JournalError):
    """Raised when an unclosed session has an ambiguous idle period requiring reconciliation."""
    pass


class DevelopmentJournalManager:
    """
    Manages development session lifecycles, breaks, persistence, and reporting.
    Guarantees atomic file writes and audit integrity with zero backfilled data.
    """

    def __init__(
        self,
        journal_dir: Optional[Path] = None,
        idle_ambiguity_threshold_sec: float = 4 * 3600,  # 4 hours
    ):
        self.journal_dir = journal_dir or (Path(__file__).resolve().parent.parent / "logs" / "journal")
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.active_file = self.journal_dir / "active_session.json"
        self.history_file = self.journal_dir / "sessions.json"
        self.idle_ambiguity_threshold_sec = idle_ambiguity_threshold_sec

    # -------------------------------------------------------------------------
    # Atomic File I/O
    # -------------------------------------------------------------------------

    def _atomic_write_json(self, file_path: Path, data: Any) -> None:
        """Writes JSON data atomically to disk via a temporary file and atomic replace."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = file_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, file_path)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink()
            raise

    def _read_json(self, file_path: Path, default: Any = None) -> Any:
        """Safely reads JSON from disk, returning default if file does not exist."""
        if not file_path.exists():
            return default
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    def get_current_git_commit(self) -> Optional[str]:
        """Safely extracts current short Git commit hash if in a Git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0:
                commit = res.stdout.strip()
                return commit if commit else None
        except Exception:
            pass
        return None

    # -------------------------------------------------------------------------
    # Active Session Inspection
    # -------------------------------------------------------------------------

    def get_active_session(self) -> Optional[DevelopmentSession]:
        """Returns the current active/paused session if present on disk."""
        data = self._read_json(self.active_file)
        if data:
            return DevelopmentSession.from_dict(data)
        return None

    def is_session_ambiguous(self, session: DevelopmentSession, now: Optional[datetime] = None) -> Tuple[bool, float]:
        """
        Determines if an open session has been left unattended beyond the ambiguity threshold.
        Returns (is_ambiguous, elapsed_sec).
        """
        now_dt = now or datetime.now(timezone.utc)
        durations = session.compute_durations(now_dt)
        elapsed = durations["total_elapsed_sec"]
        is_ambiguous = elapsed > self.idle_ambiguity_threshold_sec
        return is_ambiguous, elapsed

    # -------------------------------------------------------------------------
    # Lifecycle Commands: Start, Break, Resume, Stop
    # -------------------------------------------------------------------------

    def start_session(
        self,
        activity: str = "",
        milestone: str = "",
        start_dt: Optional[datetime] = None,
        force_reconcile: bool = False,
    ) -> DevelopmentSession:
        """
        Begins a new development session.
        Prevents duplicate starts and detects unclosed ambiguous sessions.
        """
        existing = self.get_active_session()
        if existing:
            now_dt = start_dt or datetime.now(timezone.utc)
            ambiguous, elapsed = self.is_session_ambiguous(existing, now_dt)
            if ambiguous and not force_reconcile:
                raise AmbiguousSessionError(
                    f"An unclosed session '{existing.session_id}' started at {existing.start_time} "
                    f"has been open for {format_duration(elapsed)} (exceeds ambiguity threshold). "
                    "To protect audit integrity, please reconcile this session before starting a new one. "
                    "Use: .venv/bin/python3 -m journal reconcile"
                )
            raise JournalStateError(
                f"A development session is already {existing.status} "
                f"(Session ID: {existing.session_id}, started at {existing.start_time}). "
                "Run 'status', 'break', or 'stop' before starting a new session."
            )

        now = start_dt or datetime.now(timezone.utc)
        session_id = now.strftime("%Y%m%d_%H%M%S")
        session = DevelopmentSession(
            session_id=session_id,
            start_time=now.isoformat(),
            status="ACTIVE",
            activity=activity.strip(),
            milestone=milestone.strip(),
        )

        commit = self.get_current_git_commit()
        if commit:
            session.git_commits.append(commit)

        self._atomic_write_json(self.active_file, session.to_dict())
        return session

    def take_break(
        self,
        reason: str = "",
        break_dt: Optional[datetime] = None,
    ) -> BreakInterval:
        """
        Pauses the active session and begins a recorded break interval.
        """
        session = self.get_active_session()
        if not session:
            raise JournalStateError("No active development session found to pause. Run 'start' first.")
        if session.is_paused:
            curr_b = session.current_break()
            b_info = f" (started at {curr_b.start_time})" if curr_b else ""
            raise JournalStateError(f"Session is already on break{b_info}. Run 'resume' to continue work.")
        if session.is_closed:
            raise JournalStateError("Session is already closed.")

        now = break_dt or datetime.now(timezone.utc)
        break_id = f"break_{len(session.breaks) + 1}"
        new_break = BreakInterval(
            break_id=break_id,
            start_time=now.isoformat(),
            reason=reason.strip(),
        )
        session.breaks.append(new_break)
        session.status = "PAUSED"

        self._atomic_write_json(self.active_file, session.to_dict())
        return new_break

    def resume_session(
        self,
        resume_dt: Optional[datetime] = None,
    ) -> DevelopmentSession:
        """
        Resumes an active development session by closing the open break interval.
        """
        session = self.get_active_session()
        if not session:
            raise JournalStateError("No development session found to resume. Run 'start' first.")
        if session.is_active:
            raise JournalStateError("Session is already actively running (not on break).")
        if session.is_closed:
            raise JournalStateError("Session is closed. Run 'start' to begin a new session.")

        now = resume_dt or datetime.now(timezone.utc)
        curr_b = session.current_break()
        if curr_b:
            curr_b.close(now)

        session.status = "ACTIVE"
        durations = session.compute_durations(now)
        session.total_elapsed_sec = durations["total_elapsed_sec"]
        session.total_break_sec = durations["total_break_sec"]
        session.active_work_sec = durations["active_work_sec"]

        self._atomic_write_json(self.active_file, session.to_dict())
        return session

    def stop_session(
        self,
        summary: str = "",
        git_commit: Optional[str] = None,
        notes: Optional[List[str]] = None,
        stop_dt: Optional[datetime] = None,
    ) -> DevelopmentSession:
        """
        Finalizes and archives the development session, computing net active work duration.
        """
        session = self.get_active_session()
        if not session:
            raise JournalStateError("No active or paused session to stop.")

        now = stop_dt or datetime.now(timezone.utc)
        commit = git_commit or self.get_current_git_commit()
        session.close(now, summary=summary, git_commit=commit)
        if notes:
            session.notes.extend(notes)

        # Append to historical sessions
        history = self._read_json(self.history_file, default=[])
        history.append(session.to_dict())
        self._atomic_write_json(self.history_file, history)

        # Remove active session file
        if self.active_file.exists():
            self.active_file.unlink()

        return session

    # -------------------------------------------------------------------------
    # Crash Recovery & Reconciliation
    # -------------------------------------------------------------------------

    def reconcile_session(
        self,
        confirmed_end_time: Optional[str] = None,
        active_hours: Optional[float] = None,
        note: str = "",
        discard: bool = False,
    ) -> Optional[DevelopmentSession]:
        """
        Reconciles an unclosed or interrupted session with ambiguous idle periods.
        Allows setting confirmed end time or confirmed work hours, distinguishing
        actual productive work from idle periods.
        """
        session = self.get_active_session()
        if not session:
            raise JournalStateError("No active session found to reconcile.")

        if discard:
            session.status = "DISCARDED"
            session.reconciled = True
            session.reconciliation_note = note or "Session discarded by user during reconciliation."
            session.end_time = datetime.now(timezone.utc).isoformat()
            session.active_work_sec = 0.0
        elif active_hours is not None:
            # Explicit confirmed active work hours provided
            session.status = "RECONCILED"
            session.reconciled = True
            session.end_time = datetime.now(timezone.utc).isoformat()
            session.active_work_sec = round(max(0.0, float(active_hours)) * 3600.0, 2)
            t_start = datetime.fromisoformat(session.start_time)
            session.total_elapsed_sec = round((datetime.now(timezone.utc) - t_start).total_seconds(), 2)
            session.total_break_sec = max(0.0, session.total_elapsed_sec - session.active_work_sec)
            session.reconciliation_note = note or f"Manually reconciled: Confirmed {active_hours:.2f}h active work."
        elif confirmed_end_time:
            # Parse confirmed end time
            try:
                end_dt = datetime.fromisoformat(confirmed_end_time)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                raise JournalError(f"Invalid timestamp format for confirmed_end_time: '{confirmed_end_time}'")

            session.close(end_dt)
            session.status = "RECONCILED"
            session.reconciled = True
            session.reconciliation_note = note or f"Manually reconciled: Confirmed work ended at {end_dt.isoformat()}."
        else:
            raise JournalError("Reconcile requires either confirmed_end_time, active_hours, or discard=True.")

        # Persist to history
        history = self._read_json(self.history_file, default=[])
        history.append(session.to_dict())
        self._atomic_write_json(self.history_file, history)

        if self.active_file.exists():
            self.active_file.unlink()

        return session

    # -------------------------------------------------------------------------
    # Status & Reporting
    # -------------------------------------------------------------------------

    def get_status(self, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Returns live status metrics for current tracking.
        """
        now = now_dt or datetime.now(timezone.utc)
        session = self.get_active_session()
        if not session:
            history = self._read_json(self.history_file, default=[])
            last_session = DevelopmentSession.from_dict(history[-1]) if history else None
            return {
                "status": "NO_ACTIVE_SESSION",
                "session": None,
                "last_session": last_session.to_dict() if last_session else None,
            }

        durations = session.compute_durations(now)
        ambiguous, _ = self.is_session_ambiguous(session, now)

        return {
            "status": session.status,
            "session_id": session.session_id,
            "start_time": session.start_time,
            "activity": session.activity,
            "milestone": session.milestone,
            "current_break": session.current_break().to_dict() if session.current_break() else None,
            "total_elapsed_sec": durations["total_elapsed_sec"],
            "total_break_sec": durations["total_break_sec"],
            "active_work_sec": durations["active_work_sec"],
            "breaks_count": len(session.breaks),
            "is_ambiguous": ambiguous,
            "session": session.to_dict(),
        }

    def _split_interval_by_day(
        self,
        start_dt: datetime,
        end_dt: datetime,
    ) -> Dict[str, float]:
        """
        Splits a time interval spanning midnight into exact seconds per calendar day (local timezone).
        Returns mapping of YYYY-MM-DD -> seconds.
        """
        # Convert to local timezone for calendar day alignment
        local_start = start_dt.astimezone()
        local_end = end_dt.astimezone()
        day_allocations: Dict[str, float] = {}

        cur = local_start
        while cur < local_end:
            day_key = cur.strftime("%Y-%m-%d")
            # Next midnight in local time
            next_midnight = datetime(cur.year, cur.month, cur.day, tzinfo=cur.tzinfo) + timedelta(days=1)
            interval_end = min(local_end, next_midnight)
            duration_in_day = (interval_end - cur).total_seconds()

            day_allocations[day_key] = day_allocations.get(day_key, 0.0) + duration_in_day
            cur = interval_end

        return day_allocations

    def get_report(
        self,
        reference_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Generates daily, weekly, and cumulative active work totals,
        accurately handling midnight crossings and break subtractions.
        """
        now = reference_dt or datetime.now(timezone.utc)
        now_local = now.astimezone()
        today_key = now_local.strftime("%Y-%m-%d")

        # Start of current week (Monday)
        week_start_date = now_local.date() - timedelta(days=now_local.weekday())
        week_start_dt = datetime(week_start_date.year, week_start_date.month, week_start_date.day, tzinfo=now_local.tzinfo)

        history_data = self._read_json(self.history_file, default=[])
        sessions = [DevelopmentSession.from_dict(d) for d in history_data if d.get("status") != "DISCARDED"]

        # Include current active session if running
        active_sess = self.get_active_session()
        all_sessions = list(sessions)
        if active_sess and active_sess.status != "DISCARDED":
            # Clone with current live durations
            live_copy = DevelopmentSession.from_dict(active_sess.to_dict())
            durations = live_copy.compute_durations(now)
            live_copy.end_time = now.isoformat()
            live_copy.total_elapsed_sec = durations["total_elapsed_sec"]
            live_copy.total_break_sec = durations["total_break_sec"]
            live_copy.active_work_sec = durations["active_work_sec"]
            all_sessions.append(live_copy)

        daily_active_sec: Dict[str, float] = {}
        daily_break_sec: Dict[str, float] = {}
        daily_sessions_count: Dict[str, int] = {}

        cumulative_active_sec = 0.0
        cumulative_break_sec = 0.0

        for sess in all_sessions:
            t_start = datetime.fromisoformat(sess.start_time)
            t_end = datetime.fromisoformat(sess.end_time) if sess.end_time else now

            cumulative_active_sec += sess.active_work_sec
            cumulative_break_sec += sess.total_break_sec

            # Allocate elapsed seconds by calendar day
            elapsed_by_day = self._split_interval_by_day(t_start, t_end)
            total_sess_elapsed = sum(elapsed_by_day.values())

            # Allocate break seconds by day
            break_by_day: Dict[str, float] = {}
            for b in sess.breaks:
                b_start = datetime.fromisoformat(b.start_time)
                b_end = datetime.fromisoformat(b.end_time) if b.end_time else min(now, t_end)
                b_split = self._split_interval_by_day(b_start, b_end)
                for day, b_sec in b_split.items():
                    break_by_day[day] = break_by_day.get(day, 0.0) + b_sec

            for day, el_sec in elapsed_by_day.items():
                brk_sec = break_by_day.get(day, 0.0)
                act_sec = max(0.0, el_sec - brk_sec)

                daily_active_sec[day] = daily_active_sec.get(day, 0.0) + act_sec
                daily_break_sec[day] = daily_break_sec.get(day, 0.0) + brk_sec
                daily_sessions_count[day] = daily_sessions_count.get(day, 0) + 1

        # Calculate this week total
        week_active_sec = 0.0
        week_break_sec = 0.0
        for day, sec in daily_active_sec.items():
            day_dt = datetime.strptime(day, "%Y-%m-%d").date()
            if day_dt >= week_start_date:
                week_active_sec += sec
                week_break_sec += daily_break_sec.get(day, 0.0)

        return {
            "today": {
                "date": today_key,
                "active_work_sec": round(daily_active_sec.get(today_key, 0.0), 2),
                "break_sec": round(daily_break_sec.get(today_key, 0.0), 2),
                "sessions_count": daily_sessions_count.get(today_key, 0),
            },
            "this_week": {
                "week_start": week_start_date.strftime("%Y-%m-%d"),
                "active_work_sec": round(week_active_sec, 2),
                "break_sec": round(week_break_sec, 2),
            },
            "cumulative": {
                "total_active_work_sec": round(cumulative_active_sec, 2),
                "total_break_sec": round(cumulative_break_sec, 2),
                "total_sessions_count": len(all_sessions),
            },
            "daily_history": {
                d: {
                    "active_work_sec": round(daily_active_sec[d], 2),
                    "break_sec": round(daily_break_sec.get(d, 0.0), 2),
                    "sessions": daily_sessions_count.get(d, 0),
                }
                for d in sorted(daily_active_sec.keys(), reverse=True)
            },
            "recent_sessions": [s.to_dict() for s in reversed(all_sessions[-10:])],
        }
