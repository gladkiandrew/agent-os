"""The memory store: typed notes in per-agent namespaces, written only through the runtime.

`Memory/<Agent>/<type>/<id>.md` is an agent's own namespace (its charter's write
scope). `Memory/Shared/` is protected: it changes only through an approved proposal.
The store adds the memory rules (provenance, confidence, unique ids) and then hands
the bytes to `Runtime.write`, so the guard, gate, budget and feed apply unchanged.
"""
from __future__ import annotations

import os

from ..runtime import Blocked, Runtime
from .note import CONFIDENCE, Note, NoteError, fmt_time

MEMORY = "Memory"
SHARED = "Shared"


class MemoryStore:
    def __init__(self, runtime: Runtime):
        self.rt = runtime

    # ---- paths ----
    @staticmethod
    def path_for(namespace: str, note: Note) -> str:
        return f"{MEMORY}/{namespace}/{note.type}/{note.id}.md"

    # ---- reading (rebuildable from the files every time) ----
    def load(self, include_archived: bool = False) -> dict[str, Note]:
        """Every live note by id. `Archive/` is excluded unless asked for."""
        roots = [MEMORY] + ([f"Archive/{MEMORY}"] if include_archived else [])
        notes: dict[str, Note] = {}
        for base in roots:
            top = os.path.join(self.rt.root, base)
            for dirpath, _, files in sorted(os.walk(top)):
                for fn in sorted(files):
                    if not fn.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, fn), self.rt.root).replace(os.sep, "/")
                    note = Note.from_markdown(self.rt.read(rel) or "", path=rel)
                    notes.setdefault(note.id, note)
        return notes

    def get(self, note_id: str) -> Note | None:
        return self.load().get(note_id)

    def text_of(self, note: Note) -> str:
        return self.rt.read(note.path) or ""

    # ---- rules ----
    def _check(self, agent: str, path: str, note: Note) -> None:
        note.validate()
        if note.source.startswith("agent:") and note.source.split("/")[0] != f"agent:{agent}":
            raise NoteError(f"{agent} may not write a note sourced to {note.source!r}")
        existing = self.load()
        clash = existing.get(note.id)
        if clash is not None and clash.path != path:
            raise NoteError(f"note id {note.id!r} already exists at {clash.path}")
        current = self.rt.read(path)
        if current is not None:
            old = Note.from_markdown(current, path=path)
            if CONFIDENCE[note.confidence] < CONFIDENCE.get(old.confidence, 0):
                raise NoteError(f"{note.confidence} note may not overwrite {old.confidence} "
                                f"note {old.id!r} (source {old.source})")
            note.created = note.created or old.created

    def _stamp(self, note: Note) -> None:
        now = fmt_time(self.rt.clock())
        note.created = note.created or now
        note.updated = now

    def _refuse(self, agent: str, verb: str, note: Note, err: Exception):
        self.rt.feed.log(agent, f"BLOCKED {verb} {note.type} {note.id or '(no id)'} ({err})")
        raise Blocked(str(err)) from err

    # ---- writing ----
    def remember(self, agent: str, note: Note) -> str:
        """Write a note into the agent's own namespace."""
        path = self.path_for(agent, note)
        try:
            self._check(agent, path, note)
        except NoteError as e:
            self._refuse(agent, "remember", note, e)
        self._stamp(note)
        return self.rt.write(agent, path, note.to_markdown())

    def propose_shared(self, agent: str, note: Note, reason: str):
        """Shared memory changes only by proposal; the same rules apply before filing."""
        path = self.path_for(SHARED, note)
        try:
            self._check(agent, path, note)
        except NoteError as e:
            self._refuse(agent, "propose", note, e)
        self._stamp(note)
        p = self.rt.proposals.propose(agent, path, note.to_markdown(), reason)
        self.rt.feed.log(agent, f"proposed #{p.id}: {reason}", artifact=path)
        return p

    def apply(self, steward: str, proposal_id: int) -> str:
        """Write an approved proposal. The runtime re-checks the approval and the hash."""
        p = self.rt.proposals.by_id[proposal_id]
        return self.rt.write(steward, p.path, p.content)
