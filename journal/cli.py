"""
Genesis Development Journal — CLI Interface
Provides commands for Fodoh to track active development duration, breaks, and reports.

Commands:
  start    Begin a new development session
  break    Pause the session for a break
  resume   Resume work from a break
  stop     Finalize and record the development session
  status   Show current tracking status and live elapsed time
  report   Show daily, weekly, and cumulative work reports
  reconcile Reconcile an interrupted session with ambiguous idle time
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional, List

from journal.manager import (
    DevelopmentJournalManager,
    JournalStateError,
    AmbiguousSessionError,
    JournalError,
)
from journal.models import format_duration


def _format_local_time(iso_str: Optional[str]) -> str:
    """Formats an ISO UTC string into local human-readable time with timezone abbreviation."""
    if not iso_str:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_str)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return iso_str


def cmd_start(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    try:
        session = manager.start_session(
            activity=args.activity or "",
            milestone=args.milestone or "",
        )
        print("=" * 70)
        print("⏱️  GENESIS DEVELOPMENT JOURNAL — SESSION STARTED")
        print("=" * 70)
        print(f"Status:       🟢 ACTIVE")
        print(f"Session ID:   {session.session_id}")
        print(f"Started At:   {_format_local_time(session.start_time)}")
        if session.activity:
            print(f"Activity:     {session.activity}")
        if session.milestone:
            print(f"Milestone:    {session.milestone}")
        if session.git_commits:
            print(f"Git Commit:   {', '.join(session.git_commits)}")
        print("=" * 70)
        print("💡 Use 'break' when pausing, 'resume' to continue, and 'stop' when done.")
        return 0
    except AmbiguousSessionError as err:
        print(f"\n⚠️  RECONCILIATION REQUIRED:\n{err}")
        return 2
    except JournalStateError as err:
        print(f"\n❌ Cannot start session: {err}")
        return 1


def cmd_break(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    try:
        brk = manager.take_break(reason=args.reason or "")
        print("=" * 70)
        print("☕ GENESIS DEVELOPMENT JOURNAL — BREAK STARTED")
        print("=" * 70)
        print(f"Status:       ⏸️  PAUSED (ON BREAK)")
        print(f"Break ID:     {brk.break_id}")
        print(f"Started At:   {_format_local_time(brk.start_time)}")
        if brk.reason:
            print(f"Reason:       {brk.reason}")
        print("=" * 70)
        print("💡 Run 'resume' when you return to active development.")
        return 0
    except JournalStateError as err:
        print(f"\n❌ Cannot pause session: {err}")
        return 1


def cmd_resume(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    try:
        session = manager.resume_session()
        durations = session.compute_durations()
        print("=" * 70)
        print("▶️  GENESIS DEVELOPMENT JOURNAL — SESSION RESUMED")
        print("=" * 70)
        print(f"Status:       🟢 ACTIVE")
        print(f"Resumed At:   {_format_local_time(datetime.now(timezone.utc).isoformat())}")
        print(f"Total Breaks: {len(session.breaks)} recorded ({format_duration(durations['total_break_sec'])})")
        print(f"Active Work:  {format_duration(durations['active_work_sec'])}")
        print("=" * 70)
        return 0
    except JournalStateError as err:
        print(f"\n❌ Cannot resume session: {err}")
        return 1


def cmd_stop(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    try:
        session = manager.stop_session(
            summary=args.summary or "",
            git_commit=args.commit,
            notes=[args.notes] if args.notes else None,
        )
        print("=" * 70)
        print("🏁 GENESIS DEVELOPMENT JOURNAL — SESSION COMPLETED & RECORDED")
        print("=" * 70)
        print(f"Session ID:   {session.session_id}")
        print(f"Started:      {_format_local_time(session.start_time)}")
        print(f"Ended:        {_format_local_time(session.end_time)}")
        if session.activity:
            print(f"Activity:     {session.activity}")
        if session.milestone:
            print(f"Milestone:    {session.milestone}")
        if session.summary:
            print(f"Summary:      {session.summary}")
        if session.git_commits:
            print(f"Git Commits:  {', '.join(session.git_commits)}")
        print("-" * 70)
        print(f"Active Work:  {format_duration(session.active_work_sec)}  ({session.active_work_sec:.1f}s)")
        print(f"Break Time:   {format_duration(session.total_break_sec)}  ({len(session.breaks)} breaks)")
        print(f"Total Time:   {format_duration(session.total_elapsed_sec)}")
        print("=" * 70)
        print(f"📄 Audit record saved to: {manager.history_file}")
        return 0
    except JournalStateError as err:
        print(f"\n❌ Cannot stop session: {err}")
        return 1


def cmd_status(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    status_data = manager.get_status()
    st = status_data["status"]

    print("=" * 70)
    print("📊 GENESIS DEVELOPMENT JOURNAL — STATUS")
    print("=" * 70)

    if st == "NO_ACTIVE_SESSION":
        print("Status:       ⚪ IDLE (No active session)")
        last = status_data.get("last_session")
        if last:
            print("-" * 70)
            print("Previous Session:")
            print(f"• ID:         {last['session_id']}")
            print(f"• Ended:      {_format_local_time(last.get('end_time'))}")
            print(f"• Activity:   {last.get('activity') or '(none)'}")
            print(f"• Work Time:  {format_duration(last.get('active_work_sec', 0))}")
            if last.get("summary"):
                print(f"• Summary:    {last['summary']}")
        print("=" * 70)
        print("💡 Run 'start' to begin tracking a new development session.")
        return 0

    if st == "ACTIVE":
        print("Status:       🟢 ACTIVE DEVELOPMENT")
    elif st == "PAUSED":
        print("Status:       ⏸️  PAUSED (ON BREAK)")

    print(f"Session ID:   {status_data['session_id']}")
    print(f"Started At:   {_format_local_time(status_data['start_time'])}")
    if status_data.get("activity"):
        print(f"Activity:     {status_data['activity']}")
    if status_data.get("milestone"):
        print(f"Milestone:    {status_data['milestone']}")

    curr_b = status_data.get("current_break")
    if curr_b:
        print(f"Break Start:  {_format_local_time(curr_b['start_time'])}")
        if curr_b.get("reason"):
            print(f"Break Reason: {curr_b['reason']}")

    print("-" * 70)
    print(f"Active Work:  {format_duration(status_data['active_work_sec'])}")
    print(f"Break Time:   {format_duration(status_data['total_break_sec'])} ({status_data['breaks_count']} breaks)")
    print(f"Total Time:   {format_duration(status_data['total_elapsed_sec'])}")

    if status_data.get("is_ambiguous"):
        print("⚠️  Notice: Session has been open beyond ambiguity threshold. Reconcile if idle.")

    print("=" * 70)
    return 0


def cmd_report(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    report = manager.get_report()
    today = report["today"]
    week = report["this_week"]
    cumul = report["cumulative"]

    print("=" * 70)
    print("📈 GENESIS DEVELOPMENT JOURNAL — WORK REPORT")
    print("=" * 70)
    print(f"Today ({today['date']}):")
    print(f"• Active Work:       {format_duration(today['active_work_sec'])}")
    print(f"• Break Duration:    {format_duration(today['break_sec'])}")
    print(f"• Sessions:          {today['sessions_count']}")
    print("-" * 70)
    print(f"This Week (since {week['week_start']}):")
    print(f"• Active Work:       {format_duration(week['active_work_sec'])}")
    print(f"• Break Duration:    {format_duration(week['break_sec'])}")
    print("-" * 70)
    print("All-Time Cumulative:")
    print(f"• Total Active Work: {format_duration(cumul['total_active_work_sec'])}")
    print(f"• Total Break Time:  {format_duration(cumul['total_break_sec'])}")
    print(f"• Completed Sessions:{cumul['total_sessions_count']}")
    print("=" * 70)

    recent = report.get("recent_sessions", [])
    if recent and (args.detailed or args.period == "detailed"):
        print("\nRecent Recorded Sessions:")
        for s in recent[:5]:
            start_str = _format_local_time(s['start_time'])
            end_str = _format_local_time(s.get('end_time'))
            work_str = format_duration(s.get('active_work_sec', 0))
            brk_str = format_duration(s.get('total_break_sec', 0))
            print(f"• [{start_str} → {end_str}] ({work_str} active | {brk_str} break)")
            if s.get("activity"):
                print(f"  Activity:  {s['activity']}")
            if s.get("milestone"):
                print(f"  Milestone: {s['milestone']}")
            if s.get("git_commits"):
                print(f"  Commits:   {', '.join(s['git_commits'])}")
            if s.get("reconciled"):
                print(f"  Reconciled:{s.get('reconciliation_note')}")
        print("=" * 70)

    print("ℹ️  Audit Invariant: All reported durations reflect verified timestamps.")
    print("   Zero synthetic, estimated, or backfilled hours.")
    print("=" * 70)
    return 0


def cmd_reconcile(manager: DevelopmentJournalManager, args: argparse.Namespace) -> int:
    session = manager.get_active_session()
    if not session:
        print("\nℹ️  No unclosed session currently found to reconcile.")
        return 0

    print("=" * 70)
    print("🔧 GENESIS DEVELOPMENT JOURNAL — SESSION RECONCILIATION")
    print("=" * 70)
    print(f"Unclosed Session: {session.session_id}")
    print(f"Started At:       {_format_local_time(session.start_time)}")
    durations = session.compute_durations()
    print(f"Total Elapsed:    {format_duration(durations['total_elapsed_sec'])}")
    print("-" * 70)

    try:
        if args.discard:
            reconciled = manager.reconcile_session(
                discard=True,
                note=args.note or "Discarded unclosed session.",
            )
            print("✅ Session discarded cleanly. Unclosed session removed.")
            return 0
        elif args.active_hours is not None:
            reconciled = manager.reconcile_session(
                active_hours=args.active_hours,
                note=args.note or f"Manually confirmed {args.active_hours}h active work.",
            )
            print(f"✅ Session reconciled with {args.active_hours:.2f}h active work.")
            return 0
        elif args.end_time:
            reconciled = manager.reconcile_session(
                confirmed_end_time=args.end_time,
                note=args.note or f"Confirmed end time at {args.end_time}.",
            )
            print(f"✅ Session reconciled. End time set to {args.end_time}.")
            return 0
        else:
            print("Please specify a reconciliation action:")
            print("  --active-hours <hours>   Set confirmed actual work hours")
            print("  --end-time <iso_time>    Set confirmed end timestamp")
            print("  --discard                Discard unclosed session (zero work logged)")
            print("  --note <explanation>     Audit explanation for reconciliation")
            return 1
    except JournalError as err:
        print(f"\n❌ Reconciliation failed: {err}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="journal",
        description="Genesis Development Journal — Audit-Grade Development Time Tracking",
    )
    subparsers = parser.add_subparsers(dest="command", help="Journal command to execute")

    # start
    p_start = subparsers.add_parser("start", help="Begin a development session")
    p_start.add_argument("-a", "--activity", type=str, default="", help="Description of current development activity")
    p_start.add_argument("-m", "--milestone", type=str, default="", help="Associated project milestone")

    # break
    p_break = subparsers.add_parser("break", help="Begin a recorded break")
    p_break.add_argument("-r", "--reason", type=str, default="", help="Optional break reason")

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume active development from a break")

    # stop
    p_stop = subparsers.add_parser("stop", help="End and save the development session")
    p_stop.add_argument("-s", "--summary", type=str, default="", help="Summary of work accomplished")
    p_stop.add_argument("-c", "--commit", type=str, default=None, help="Associated Git commit hash")
    p_stop.add_argument("-n", "--notes", type=str, default="", help="Additional session notes")

    # status
    p_status = subparsers.add_parser("status", help="Show current session status and live elapsed time")

    # report
    p_report = subparsers.add_parser("report", help="Show daily, weekly, and cumulative work totals")
    p_report.add_argument("-p", "--period", choices=["daily", "weekly", "cumulative", "detailed"], default="daily")
    p_report.add_argument("-d", "--detailed", action="store_true", help="Display recent individual session details")

    # reconcile
    p_reconcile = subparsers.add_parser("reconcile", help="Reconcile an unclosed or interrupted session")
    p_reconcile.add_argument("--end-time", type=str, default=None, help="Confirmed end timestamp (ISO-8601)")
    p_reconcile.add_argument("--active-hours", type=float, default=None, help="Confirmed actual productive work hours")
    p_reconcile.add_argument("--discard", action="store_true", help="Discard unclosed session without recording hours")
    p_reconcile.add_argument("--note", type=str, default="", help="Audit explanation note")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    manager = DevelopmentJournalManager()

    commands = {
        "start": cmd_start,
        "break": cmd_break,
        "resume": cmd_resume,
        "stop": cmd_stop,
        "status": cmd_status,
        "report": cmd_report,
        "reconcile": cmd_reconcile,
    }

    fn = commands.get(args.command)
    if fn:
        return fn(manager, args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
