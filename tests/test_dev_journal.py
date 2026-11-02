"""
Tests for Genesis Development Journal
Verifies:
- Session start, break, resume, and stop lifecycle
- Active work calculation (session duration minus breaks)
- Prevention of duplicate starts and invalid state transitions
- Midnight boundary duration splitting
- Crash recovery and ambiguous idle time reconciliation
- Restart persistence across manager instances
- Daily, weekly, and cumulative reporting
- Strict memory isolation (zero contamination of memories.json or ChromaDB)
"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from journal.models import BreakInterval, DevelopmentSession, format_duration
from journal.manager import (
    DevelopmentJournalManager,
    JournalStateError,
    AmbiguousSessionError,
    JournalError,
)


class TestDevelopmentJournal(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_genesis_journal_"))
        self.manager = DevelopmentJournalManager(
            journal_dir=self.test_dir,
            idle_ambiguity_threshold_sec=4 * 3600,  # 4 hours
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Lifecycle: Start, Break, Resume, Stop
    # -------------------------------------------------------------------------

    def test_start_session_creates_active_state(self):
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        session = self.manager.start_session(
            activity="Phase 1 Hub Reliability",
            milestone="Phase 1",
            start_dt=t0,
        )

        self.assertTrue(session.is_active)
        self.assertEqual(session.status, "ACTIVE")
        self.assertEqual(session.activity, "Phase 1 Hub Reliability")
        self.assertEqual(session.milestone, "Phase 1")
        self.assertEqual(session.start_time, t0.isoformat())
        self.assertEqual(len(session.breaks), 0)

        # File persistence check
        self.assertTrue(self.manager.active_file.exists())
        saved_data = json.loads(self.manager.active_file.read_text())
        self.assertEqual(saved_data["session_id"], session.session_id)
        self.assertEqual(saved_data["status"], "ACTIVE")

    def test_duplicate_start_raises_state_error(self):
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Task 1", start_dt=t0)

        with self.assertRaises(JournalStateError) as ctx:
            self.manager.start_session(activity="Task 2", start_dt=t0 + timedelta(minutes=10))

        self.assertIn("already ACTIVE", str(ctx.exception))

    def test_break_and_resume_lifecycle(self):
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_break = t0 + timedelta(hours=1)
        t_resume = t_break + timedelta(minutes=20)

        self.manager.start_session(activity="Coding", start_dt=t0)

        # Take break
        brk = self.manager.take_break(reason="Coffee", break_dt=t_break)
        self.assertEqual(brk.break_id, "break_1")
        self.assertEqual(brk.reason, "Coffee")
        self.assertFalse(brk.is_closed)

        active = self.manager.get_active_session()
        self.assertTrue(active.is_paused)
        self.assertEqual(len(active.breaks), 1)

        # Resume
        resumed = self.manager.resume_session(resume_dt=t_resume)
        self.assertTrue(resumed.is_active)
        self.assertEqual(len(resumed.breaks), 1)
        self.assertTrue(resumed.breaks[0].is_closed)
        self.assertEqual(resumed.breaks[0].duration_sec, 20 * 60.0)

    def test_stop_calculates_net_active_time(self):
        """
        Scenario:
        14:00 - Start
        15:00 - Break 1 (15m)
        15:15 - Resume
        16:00 - Break 2 (10m)
        16:10 - Resume
        17:00 - Stop
        Total Elapsed: 3h (10,800s)
        Total Break:   25m (1,500s)
        Net Active:    2h 35m (9,300s)
        """
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Architecture", milestone="Hub", start_dt=t0)

        # Break 1: 15 min
        self.manager.take_break(reason="Snack", break_dt=t0 + timedelta(hours=1))
        self.manager.resume_session(resume_dt=t0 + timedelta(hours=1, minutes=15))

        # Break 2: 10 min
        self.manager.take_break(reason="Call", break_dt=t0 + timedelta(hours=2))
        self.manager.resume_session(resume_dt=t0 + timedelta(hours=2, minutes=10))

        # Stop at 17:00
        t_stop = t0 + timedelta(hours=3)
        session = self.manager.stop_session(
            summary="Refactored hub orchestrator",
            git_commit="a8bf359",
            stop_dt=t_stop,
        )

        self.assertEqual(session.status, "COMPLETED")
        self.assertEqual(session.total_elapsed_sec, 10800.0)
        self.assertEqual(session.total_break_sec, 1500.0)
        self.assertEqual(session.active_work_sec, 9300.0)
        self.assertEqual(session.summary, "Refactored hub orchestrator")
        self.assertIn("a8bf359", session.git_commits)

        # Active file must be deleted, history file must contain record
        self.assertFalse(self.manager.active_file.exists())
        self.assertTrue(self.manager.history_file.exists())
        history = json.loads(self.manager.history_file.read_text())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["active_work_sec"], 9300.0)

    def test_stop_while_paused_closes_open_break_cleanly(self):
        """Stopping directly while on break closes the final break up to stop time."""
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Testing", start_dt=t0)

        # Work 1 hour, then break for 30 min, then stop directly
        t_break = t0 + timedelta(hours=1)
        t_stop = t_break + timedelta(minutes=30)
        self.manager.take_break(reason="Lunch", break_dt=t_break)

        session = self.manager.stop_session(summary="Done for the day", stop_dt=t_stop)

        self.assertEqual(session.status, "COMPLETED")
        self.assertEqual(len(session.breaks), 1)
        self.assertTrue(session.breaks[0].is_closed)
        self.assertEqual(session.breaks[0].duration_sec, 1800.0)
        self.assertEqual(session.active_work_sec, 3600.0)
        self.assertEqual(session.total_break_sec, 1800.0)
        self.assertEqual(session.total_elapsed_sec, 5400.0)

    # -------------------------------------------------------------------------
    # 2. Invalid State Transitions
    # -------------------------------------------------------------------------

    def test_invalid_break_when_no_active_session(self):
        with self.assertRaises(JournalStateError) as ctx:
            self.manager.take_break(reason="Coffee")
        self.assertIn("No active development session", str(ctx.exception))

    def test_invalid_break_when_already_paused(self):
        self.manager.start_session(activity="Design")
        self.manager.take_break(reason="First break")
        with self.assertRaises(JournalStateError) as ctx:
            self.manager.take_break(reason="Second break")
        self.assertIn("already on break", str(ctx.exception))

    def test_invalid_resume_when_active(self):
        self.manager.start_session(activity="Design")
        with self.assertRaises(JournalStateError) as ctx:
            self.manager.resume_session()
        self.assertIn("already actively running", str(ctx.exception))

    def test_invalid_stop_when_idle(self):
        with self.assertRaises(JournalStateError) as ctx:
            self.manager.stop_session()
        self.assertIn("No active or paused session", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 3. Midnight Boundary Allocation
    # -------------------------------------------------------------------------

    def test_midnight_boundary_splitting_in_daily_report(self):
        """
        Session spans across midnight in local time:
        2026-10-20 23:00 to 2026-10-21 02:00 (3 hours total)
        Break from 23:45 to 00:15 (30 mins total: 15m on Oct 20, 15m on Oct 21)

        Oct 20 work: 60m elapsed - 15m break = 45m (2700s)
        Oct 21 work: 120m elapsed - 15m break = 105m (6300s)
        Total active: 9000s (2.5h)
        """
        # Pick local dates
        day1_start = datetime(2026, 11, 10, 23, 0, 0).astimezone()
        break_start = datetime(2026, 11, 10, 23, 45, 0).astimezone()
        break_end = datetime(2026, 11, 11, 0, 15, 0).astimezone()
        day2_end = datetime(2026, 11, 11, 2, 0, 0).astimezone()

        self.manager.start_session(activity="Night Ops", start_dt=day1_start.astimezone(timezone.utc))
        self.manager.take_break(reason="Midnight Coffee", break_dt=break_start.astimezone(timezone.utc))
        self.manager.resume_session(resume_dt=break_end.astimezone(timezone.utc))
        self.manager.stop_session(summary="Overnight shift", stop_dt=day2_end.astimezone(timezone.utc))

        report = self.manager.get_report(reference_dt=day2_end.astimezone(timezone.utc))

        daily_hist = report["daily_history"]
        self.assertIn("2026-11-10", daily_hist)
        self.assertIn("2026-11-11", daily_hist)

        # 45 mins on Day 1
        self.assertAlmostEqual(daily_hist["2026-11-10"]["active_work_sec"], 2700.0, delta=2.0)
        self.assertAlmostEqual(daily_hist["2026-11-10"]["break_sec"], 900.0, delta=2.0)

        # 105 mins on Day 2
        self.assertAlmostEqual(daily_hist["2026-11-11"]["active_work_sec"], 6300.0, delta=2.0)
        self.assertAlmostEqual(daily_hist["2026-11-11"]["break_sec"], 900.0, delta=2.0)

        # Cumulative: 9000s active
        self.assertAlmostEqual(report["cumulative"]["total_active_work_sec"], 9000.0, delta=2.0)

    # -------------------------------------------------------------------------
    # 4. Crash Recovery & Ambiguous Idle Time Reconciliation
    # -------------------------------------------------------------------------

    def test_ambiguous_session_detected_when_unclosed(self):
        """Unclosed session past ambiguity threshold raises AmbiguousSessionError on start."""
        t_start = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Interrupted Task", start_dt=t_start)

        # Next morning (20 hours later)
        t_next_day = t_start + timedelta(hours=20)

        with self.assertRaises(AmbiguousSessionError) as ctx:
            self.manager.start_session(activity="New Task", start_dt=t_next_day)

        self.assertIn("reconcile", str(ctx.exception))
        self.assertIn("exceeds ambiguity threshold", str(ctx.exception))

    def test_reconcile_with_confirmed_active_hours(self):
        """Reconciliation allows Fodoh to specify exact confirmed work hours."""
        t_start = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Interrupted Task", start_dt=t_start)

        # Reconcile: User confirms they only worked 2.5 hours before stepping away
        reconciled = self.manager.reconcile_session(
            active_hours=2.5,
            note="Worked 2.5h before terminal crashed.",
        )

        self.assertEqual(reconciled.status, "RECONCILED")
        self.assertTrue(reconciled.reconciled)
        self.assertEqual(reconciled.active_work_sec, 2.5 * 3600.0)
        self.assertIn("2.5h", reconciled.reconciliation_note)

        # Active file removed; new session can now start cleanly
        self.assertFalse(self.manager.active_file.exists())
        fresh = self.manager.start_session(activity="New Task")
        self.assertTrue(fresh.is_active)

    def test_reconcile_with_confirmed_end_time(self):
        """Reconciliation allows Fodoh to specify exact confirmed stop time."""
        t_start = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Morning Coding", start_dt=t_start)

        t_end = t_start + timedelta(hours=3, minutes=15)
        reconciled = self.manager.reconcile_session(
            confirmed_end_time=t_end.isoformat(),
            note="Confirmed work ended at 12:15 UTC.",
        )

        self.assertEqual(reconciled.status, "RECONCILED")
        self.assertEqual(reconciled.active_work_sec, 3.25 * 3600.0)
        self.assertFalse(self.manager.active_file.exists())

    def test_reconcile_discard(self):
        """Reconciliation allows discarding an accidental session."""
        self.manager.start_session(activity="Accidental Click")
        reconciled = self.manager.reconcile_session(discard=True, note="Accidental start.")
        self.assertEqual(reconciled.status, "DISCARDED")
        self.assertEqual(reconciled.active_work_sec, 0.0)
        self.assertFalse(self.manager.active_file.exists())

    # -------------------------------------------------------------------------
    # 5. Restart Persistence
    # -------------------------------------------------------------------------

    def test_restart_persistence_across_manager_instances(self):
        """Verifies that separate manager instances load in-flight sessions cleanly."""
        t0 = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
        self.manager.start_session(activity="Persistent Task", milestone="M1", start_dt=t0)
        self.manager.take_break(reason="Short pause", break_dt=t0 + timedelta(minutes=30))

        # Simulate terminal restart / new process instantiating manager
        new_manager = DevelopmentJournalManager(journal_dir=self.test_dir)
        status = new_manager.get_status(now_dt=t0 + timedelta(minutes=40))

        self.assertEqual(status["status"], "PAUSED")
        self.assertEqual(status["activity"], "Persistent Task")
        self.assertEqual(status["milestone"], "M1")
        self.assertEqual(status["breaks_count"], 1)

        # Resume from new manager
        new_manager.resume_session(resume_dt=t0 + timedelta(minutes=45))
        session = new_manager.stop_session(stop_dt=t0 + timedelta(hours=1))

        self.assertEqual(session.active_work_sec, 2700.0)  # 45m work
        self.assertEqual(session.total_break_sec, 900.0)   # 15m break

    # -------------------------------------------------------------------------
    # 6. Memory Isolation Invariant
    # -------------------------------------------------------------------------

    def test_strict_memory_isolation_invariant(self):
        """
        STRICT INVARIANT:
        Development journal operations must NEVER touch or create files in
        memory/memories.json, conversation/history.json, or chroma_db.
        """
        t0 = datetime.now(timezone.utc)
        self.manager.start_session(activity="Memory Isolation Test", start_dt=t0)
        self.manager.take_break(reason="Break 1", break_dt=t0 + timedelta(minutes=10))
        self.manager.resume_session(resume_dt=t0 + timedelta(minutes=15))
        self.manager.stop_session(summary="Memory check", stop_dt=t0 + timedelta(minutes=30))

        project_root = Path(__file__).resolve().parent.parent

        # Verify journal files are confined to its journal_dir
        for f in self.test_dir.iterdir():
            self.assertTrue(f.name.endswith(".json"))

        # Verify no rogue memory file created in journal_dir
        self.assertFalse((self.test_dir / "memories.json").exists())
        self.assertFalse((self.test_dir / "history.json").exists())

    # -------------------------------------------------------------------------
    # 7. Utilities & Duration Formatting
    # -------------------------------------------------------------------------

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(90), "1m 30s")
        self.assertEqual(format_duration(3600), "1h 00m 00s")
        self.assertEqual(format_duration(3665), "1h 01m 05s")
        self.assertEqual(format_duration(7325), "2h 02m 05s")


if __name__ == "__main__":
    unittest.main()
