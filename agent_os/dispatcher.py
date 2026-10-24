"""Dispatcher: the heartbeat. It routes work; it never does the work.

Each tick it:
  1. promotes new inbox captures to tasks (quarantined first; suspicious ones are parked)
  2. escalates tasks that have sat in `doing` too long
  3. returns ONE status line, only if something changed. Silence means nothing moved.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field

from .quarantine import quarantine

STALL_AFTER = dt.timedelta(hours=24)


@dataclass
class Task:
    id: int
    title: str
    assignee: str | None = None
    status: str = "todo"                  # todo | doing | done | parked
    since: dt.datetime | None = None
    flags: tuple = ()


@dataclass
class Board:
    tasks: list = field(default_factory=list)
    inbox: list = field(default_factory=list)   # (source, text, suggested_assignee)

    def next_id(self) -> int:
        return max((t.id for t in self.tasks), default=0) + 1


def tick(board: Board, now: dt.datetime, roster: set, escalated: set) -> str | None:
    changes = []
    while board.inbox:
        source, text, who = board.inbox.pop(0)
        q = quarantine(text, source)
        title = text.strip().splitlines()[0][:80] if text.strip() else "(empty capture)"
        if q.suspicious:
            board.tasks.append(Task(board.next_id(), title, None, "parked", now, q.flags))
            changes.append(f"parked a capture from {source} ({', '.join(q.flags)})")
            continue
        assignee = who if who in roster else None
        board.tasks.append(Task(board.next_id(), title, assignee, "todo", now))
        changes.append(f"promoted '{title}'" + (f" → {assignee}" if assignee else " (unassigned)"))
    for t in board.tasks:
        if t.status == "doing" and t.since and now - t.since > STALL_AFTER and t.id not in escalated:
            escalated.add(t.id)
            changes.append(f"STALL: #{t.id} '{t.title}' ({t.assignee}) in doing for {(now - t.since).days}d+")
    return ("Dispatcher: " + "; ".join(changes)) if changes else None
