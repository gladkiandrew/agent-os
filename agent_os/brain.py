"""The Brain: one object that wires the second brain onto the existing runtime.

It owns nothing new. The runtime still does every write; the brain just holds the
memory store, board and bus that sit on top of it, so callers (the run loop, the
demo, a real deployment) don't have to assemble them by hand.
"""
from __future__ import annotations

import datetime as dt

from .charter import load_charters
from .coord.board import Board, Task
from .coord.bus import MessageBus
from .coord import dispatcher
from .memory.consolidate import Report, consolidate
from .memory.context import ContextPack, build_context
from .memory.store import MemoryStore
from .runtime import Runtime


class Brain:
    def __init__(self, root: str, clock=None, steward: str = "Captain",
                 lease: dt.timedelta = dt.timedelta(hours=2), charters: dict | None = None):
        charters = charters if charters is not None else load_charters(f"{root}/Agents")
        self.rt = Runtime(charters, root, clock=clock)
        self.memory = MemoryStore(self.rt)
        self.board = Board(self.rt, steward=steward, lease=lease)
        self.bus = MessageBus(self.rt, steward=steward)
        self.steward = steward
        self._escalated: set = set()

    @property
    def clock(self):
        return self.rt.clock

    def build_context(self, agent: str, task: Task | str, budget_chars: int = 2000) -> ContextPack:
        """The task's handoff first, then the best-matching notes, cited and inside the budget."""
        notes = self.memory.load()
        texts = {i: self.memory.text_of(n) for i, n in notes.items()}
        title = task.title if isinstance(task, Task) else task
        handoffs = (task.handoff,) if isinstance(task, Task) and task.handoff else ()
        return build_context(agent, title, notes, texts, budget_chars, self.clock(), handoff_ids=handoffs)

    def tick(self) -> str | None:
        return dispatcher.tick(self.board, self.bus, self._escalated)

    def consolidate(self, **kw) -> Report:
        return consolidate(self.memory, self.steward, **kw)
