"""The task board: one markdown note per task, changed only through checked transitions.

Agents never edit `Tasks/` themselves. They ask the board to claim, start, hand off
or submit, the board checks the rules, and one steward agent (the Captain, whose
charter owns `Tasks/`) persists the result through the runtime. That gives a single
writer per file: the same rule the crew follows everywhere else.

A claim is a lease. It expires, so a crashed agent can't hold work forever, and the
board refuses a claim while another agent's lease is live. A lock serializes
transitions within the process; a multi-process deployment would put this board
behind one service instead of sharing files.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
from dataclasses import dataclass, field

from ..charter import parse_frontmatter
from ..memory.note import fmt_time, parse_time
from ..runtime import Runtime

STATUSES = ("todo", "claimed", "doing", "review", "done", "parked")
HELD = ("claimed", "doing")
TASKS = "Tasks"


class ClaimRefused(PermissionError):
    pass


@dataclass
class Task:
    id: str
    title: str
    owner: str = ""
    status: str = "todo"
    needs: tuple = ()
    depends_on: tuple = ()
    due: str = ""
    lease_until: str = ""
    since: str = ""            # when the status last changed (stall detection)
    handoff: str = ""          # note id of the latest handoff
    origin: str = ""           # message id this task was promoted from
    history: list = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"{TASKS}/{self.id}.md"

    def to_markdown(self) -> str:
        lines = ["---", f"id: {self.id}", f"title: {self.title}", f"status: {self.status}"]
        for key in ("owner", "due", "lease_until", "since", "handoff", "origin"):
            if getattr(self, key):
                lines.append(f"{key}: {getattr(self, key)}")
        for key in ("needs", "depends_on"):
            lines.append(f"{key}:")
            lines += [f"  - {v}" for v in getattr(self, key)]
        lines += ["---", "", f"# {self.title}", "", "## History", ""]
        return "\n".join(lines + self.history) + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> "Task":
        meta, body = parse_frontmatter(text)
        history = [l for l in body.splitlines() if l.startswith("- ")]
        return cls(id=meta["id"], title=meta["title"], owner=meta.get("owner", ""),
                   status=meta.get("status", "todo"), needs=tuple(meta.get("needs", []) or ()),
                   depends_on=tuple(meta.get("depends_on", []) or ()), due=meta.get("due", ""),
                   lease_until=meta.get("lease_until", ""), since=meta.get("since", ""),
                   handoff=meta.get("handoff", ""), origin=meta.get("origin", ""), history=history)


class Board:
    def __init__(self, runtime: Runtime, steward: str = "Captain",
                 lease: dt.timedelta = dt.timedelta(hours=2)):
        self.rt, self.steward, self.lease = runtime, steward, lease
        self._lock = threading.Lock()
        self.heartbeats: dict[str, dt.datetime] = {}

    # ---- reading ----
    def tasks(self) -> dict[str, Task]:
        top = os.path.join(self.rt.root, TASKS)
        out = {}
        for fn in sorted(os.listdir(top)) if os.path.isdir(top) else []:
            if fn.endswith(".md"):
                t = Task.from_markdown(self.rt.read(f"{TASKS}/{fn}") or "")
                out[t.id] = t
        return out

    def get(self, task_id: str) -> Task:
        t = self.tasks().get(task_id)
        if t is None:
            raise KeyError(f"no task {task_id!r}")
        return t

    def lease_live(self, t: Task, now: dt.datetime) -> bool:
        return t.status in HELD and bool(t.lease_until) and parse_time(t.lease_until) > now

    def holds(self, agent: str, t: Task, now: dt.datetime) -> bool:
        return t.owner == agent and self.lease_live(t, now)

    # ---- persistence ----
    def _save(self, t: Task, actor: str, event: str, status_change: bool = True) -> None:
        now = self.rt.clock()
        if status_change:
            t.since = fmt_time(now)
        t.history.append(f"- {fmt_time(now)} · {actor} {event}")
        self.rt.write(self.steward, t.path, t.to_markdown(), summary=f"{t.id}: {actor} {event}")

    def _refuse(self, agent: str, task_id: str, why: str):
        self.rt.feed.log(agent, f"BLOCKED claim {task_id} ({why})")
        raise ClaimRefused(why)

    # ---- transitions ----
    def create(self, title: str, needs: tuple = (), depends_on: tuple = (), due: str = "",
               origin: str = "", owner: str = "", actor: str = "Dispatcher") -> Task:
        with self._lock:
            n = max((int(i.split("-")[1]) for i in self.tasks()), default=0) + 1
            t = Task(f"T-{n:04d}", title, owner=owner, needs=tuple(needs),
                     depends_on=tuple(depends_on), due=due, origin=origin)
            self._save(t, actor, "created" + (f" for {owner}" if owner else ""))
            return t

    def assign(self, task_id: str, agent: str, actor: str = "Dispatcher") -> Task:
        with self._lock:
            t = self.get(task_id)
            t.owner = agent
            self._save(t, actor, f"routed to {agent}", status_change=False)
            return t

    def claim(self, agent: str, task_id: str) -> Task:
        with self._lock:
            now = self.rt.clock()
            c = self.rt.charters.get(agent)
            if c is None or not c.runnable:
                self._refuse(agent, task_id, f"{agent} has no active charter")
            t = self.get(task_id)
            if self.lease_live(t, now) and t.owner != agent:
                self._refuse(agent, task_id, f"held by {t.owner} until {t.lease_until}")
            if t.status != "todo":
                self._refuse(agent, task_id, f"{t.id} is {t.status}, not todo")
            if t.owner and t.owner != agent:
                self._refuse(agent, task_id, f"{t.id} is routed to {t.owner}")
            tasks = self.tasks()
            open_deps = [d for d in t.depends_on if tasks.get(d) is None or tasks[d].status != "done"]
            if open_deps:
                self._refuse(agent, task_id, f"waiting on {', '.join(open_deps)}")
            t.owner, t.status = agent, "claimed"
            t.lease_until = fmt_time(now + self.lease)
            self.heartbeats[agent] = now
            self._save(t, agent, f"claimed (lease until {t.lease_until[11:]})")
            return t

    def _held(self, agent: str, task_id: str) -> Task:
        t = self.get(task_id)
        if not self.holds(agent, t, self.rt.clock()):
            self._refuse(agent, task_id, f"{agent} does not hold a live lease on {task_id}")
        return t

    def start(self, agent: str, task_id: str) -> Task:
        with self._lock:
            t = self._held(agent, task_id)
            t.status = "doing"
            self._save(t, agent, "started")
            return t

    def heartbeat(self, agent: str) -> None:
        self.heartbeats[agent] = self.rt.clock()

    def renew(self, task_id: str) -> Task:
        with self._lock:
            t = self.get(task_id)
            t.lease_until = fmt_time(self.rt.clock() + self.lease)
            self._save(t, "Dispatcher", f"renewed {t.owner}'s lease", status_change=False)
            return t

    def handoff(self, agent: str, task_id: str, to: str, note_id: str) -> Task:
        with self._lock:
            t = self._held(agent, task_id)
            t.owner, t.status, t.lease_until, t.handoff = to, "todo", "", note_id
            self._save(t, agent, f"handed off to {to} ([[{note_id}]])")
            return t

    def submit(self, agent: str, task_id: str, note_id: str = "") -> Task:
        with self._lock:
            t = self._held(agent, task_id)
            t.status, t.lease_until = "review", ""
            t.handoff = note_id or t.handoff
            self._save(t, agent, "submitted for review")
            return t

    def accept(self, reviewer: str, task_id: str) -> Task:
        with self._lock:
            t = self.get(task_id)
            c = self.rt.charters.get(reviewer)
            if c is None or not c.runnable or "review" not in c.allowed_actions:
                self._refuse(reviewer, task_id, f"{reviewer} is not a chartered reviewer")
            if t.status != "review":
                self._refuse(reviewer, task_id, f"{t.id} is {t.status}, not in review")
            if reviewer == t.owner:
                self._refuse(reviewer, task_id, "nobody reviews their own work")
            t.status = "done"
            self._save(t, reviewer, "accepted → done")
            return t

    def release(self, agent: str, task_id: str) -> Task:
        with self._lock:
            t = self._held(agent, task_id)
            t.owner, t.status, t.lease_until = "", "todo", ""
            self._save(t, agent, "released the claim")
            return t

    def expire(self, task_id: str) -> Task:
        with self._lock:
            t = self.get(task_id)
            who = t.owner
            t.owner, t.status, t.lease_until = "", "todo", ""
            self._save(t, "Dispatcher", f"expired {who}'s lease (no heartbeat)")
            return t

    def park(self, task_id: str, reason: str, actor: str = "Dispatcher") -> Task:
        with self._lock:
            t = self.get(task_id)
            t.status, t.lease_until = "parked", ""
            self._save(t, actor, f"parked ({reason})")
            return t
