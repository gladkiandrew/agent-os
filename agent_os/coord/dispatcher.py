"""Dispatcher v2: the heartbeat over the board, the bus and the leases. It routes; it never works.

Each tick:
  1. promotes new captures in the steward's inbox to tasks. A flagged (quarantined)
     message is parked for a human and never becomes a task.
  2. routes unowned `todo` tasks to the active agent whose charter capabilities best
     match the task, so routing is explainable from the charters alone.
  3. renews the lease of any holder that has sent a heartbeat since it was granted,
     and expires (then re-routes) any lease that ran out without one.
  4. escalates a task stuck in `doing` past the stall window, once.
  5. returns ONE status line if anything changed, else None. Renewals alone are not
     news: a renewed lease means the system is working.
"""
from __future__ import annotations

import datetime as dt

from ..memory.note import parse_time
from ..memory.search import tokenize
from .board import Board
from .bus import MessageBus

STALL_AFTER = dt.timedelta(hours=24)


def route(title: str, charters: dict) -> str:
    """Best capability match among active charters; ties break alphabetically. '' = nobody."""
    words = set(tokenize(title))
    best, best_n = "", 0
    for name in sorted(charters):
        c = charters[name]
        if not c.runnable:
            continue
        n = len(words & set(tokenize(" ".join(c.capabilities))))
        if n > best_n:
            best, best_n = name, n
    return best


def tick(board: Board, bus: MessageBus, escalated: set, stall_after: dt.timedelta = STALL_AFTER) -> str | None:
    rt = board.rt
    now = rt.clock()
    changes: list[str] = []

    # 1. captures → tasks (or parked)
    tasks = board.tasks()
    seen = {t.origin for t in tasks.values() if t.origin} | bus.parked_ids()
    for msg in bus.inbox(board.steward):
        if msg.id in seen or not msg.external:
            continue
        if msg.suspicious:
            bus.park(msg, ", ".join(msg.flags))
            rt.feed.log("Dispatcher", f"parked {msg.id} from {msg.sender} for a human ({', '.join(msg.flags)})")
            changes.append(f"parked {msg.id} ({', '.join(msg.flags)})")
            continue
        title = msg.text.splitlines()[0][:80]
        t = board.create(title, origin=msg.id)
        changes.append(f"promoted {msg.id} → {t.id}")

    # 2. route unowned work
    for t in board.tasks().values():
        if t.status == "todo" and not t.owner:
            who = route(t.title, rt.charters)
            if who:
                board.assign(t.id, who)
                changes.append(f"{t.id} → {who}")

    # 3. leases: renew on heartbeat, expire without one
    for t in board.tasks().values():
        if t.status not in ("claimed", "doing") or not t.lease_until:
            continue
        granted = parse_time(t.lease_until) - board.lease
        beat = board.heartbeats.get(t.owner)
        if beat is not None and beat > granted:
            board.renew(t.id)
        elif parse_time(t.lease_until) <= now:
            who = t.owner
            board.expire(t.id)
            nxt = route(t.title, rt.charters)
            if nxt:
                board.assign(t.id, nxt)
            changes.append(f"expired {who}'s lease on {t.id}" + (f", re-routed → {nxt}" if nxt else ""))

    # 4. stalls
    for t in board.tasks().values():
        if t.status == "doing" and t.since and now - parse_time(t.since) > stall_after and t.id not in escalated:
            escalated.add(t.id)
            changes.append(f"STALL: {t.id} '{t.title}' ({t.owner}) in doing {(now - parse_time(t.since)).days}d+")

    if not changes:
        return None
    line = "Dispatcher: " + "; ".join(changes)
    rt.feed.log("Dispatcher", line[len("Dispatcher: "):])
    return line
