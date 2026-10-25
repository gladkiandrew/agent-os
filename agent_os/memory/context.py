"""Context packs: the most relevant memory for one task, cited, inside a hard size budget.

Why a budget in characters, not "top k": the pack goes into a model's context
window next to the task, and a pack that can silently grow is how a crew starts
paying for (and being confused by) its own history. Every item carries its
citation, so whatever the agent writes back can say exactly what it relied on.
External content is passed through `quarantine()` and arrives as quoted data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..quarantine import quarantine
from .note import Note
from .search import BM25Retriever, Hit, Retriever

MIN_TAIL = 160   # don't bother squeezing in an item that would be mostly "…"


@dataclass
class PackItem:
    note_id: str
    citation: str
    why: str            # "handoff", "match" or "link:<id>"
    quarantined: bool
    text: str


@dataclass
class ContextPack:
    agent: str
    task: str
    budget_chars: int
    items: list[PackItem] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(i.text for i in self.items)

    @property
    def cited_ids(self) -> list[str]:
        return [i.note_id for i in self.items]


def _render(note: Note, citation: str, why: str) -> tuple[str, bool]:
    head = f"{citation} · {note.type} · {note.confidence} · {why}\n# {note.title}"
    if note.is_external:
        return head + "\n" + quarantine(note.body, note.source).as_prompt_block(), True
    return head + "\n" + note.body.strip(), False


def build_context(agent: str, task_title: str, notes: dict[str, Note], texts: dict[str, str],
                  budget_chars: int, now: dt.datetime, handoff_ids: tuple = (),
                  retriever: Retriever | None = None, limit: int = 8) -> ContextPack:
    if budget_chars <= 0:
        raise ValueError("budget_chars must be positive")
    retriever = retriever or BM25Retriever(notes, texts)
    pack = ContextPack(agent, task_title, budget_chars)
    candidates: list[tuple[Note, str, str]] = []
    for hid in handoff_ids:                       # the previous owner's handoff comes first
        n = notes.get(hid)
        if n is not None:
            text = texts.get(hid, "")
            line = n.body_start_line(text) if text.startswith("---") else 1
            candidates.append((n, f"[[{n.id}]] {n.path}:{line}", "handoff"))
    seen = {c[0].id for c in candidates}
    hits: list[Hit] = retriever.search(task_title, now, limit=limit)
    for h in hits:
        if h.note.id not in seen and h.note.type != "handoff":
            candidates.append((h.note, h.citation, h.via))
            seen.add(h.note.id)

    used = 0
    for note, citation, why in candidates:
        text, q = _render(note, citation, why)
        sep = 2 if pack.items else 0
        room = budget_chars - used - sep
        if len(text) > room:
            if room < max(MIN_TAIL, len(citation) + 40):
                break
            text = text[: room - 1] + "…"        # trimmed, but the citation line survives
        pack.items.append(PackItem(note.id, citation, why, q, text))
        used += sep + len(text)
    return pack
