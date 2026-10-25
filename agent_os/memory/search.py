"""Retrieval: BM25 keyword scoring + one-hop link expansion + recency, standard library only.

Why not embeddings: a small operation's vault is a few thousand notes written in
its own vocabulary, where exact terms ("Tuesday delivery", a supplier's name) carry
the signal. BM25 is deterministic, explainable and dependency-free. The `Retriever`
protocol is the seam: an embedding retriever can drop in later without touching
callers.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .note import Note, parse_time

STOPWORDS = frozenset("""a an and are as at be by for from has have in is it its of on or
that the this to was were will with we our you your they their not but if then than
can do does did so into about over after before when what which who how all any""".split())

TITLE_WEIGHT = 2      # title and tags are repeated into the term bag: they describe the note
LINK_DISCOUNT = 0.5   # a note reached through a link scores half its parent
HALF_LIFE_DAYS = 30.0
K1, B = 1.5, 0.75
# Distilled knowledge outranks raw history: a learning is what a run concluded, an
# episode is everything that happened. Both stay searchable; the learning wins ties.
TYPE_WEIGHT = {"learning": 1.4, "decision": 1.3, "fact": 1.2, "entity": 1.0,
               "handoff": 0.8, "episode": 0.6}


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1]


@dataclass(frozen=True)
class Hit:
    note: Note
    score: float
    line: int           # 1-based line in the note's file that best matches the query
    via: str            # "match" or "link:<id>" for notes pulled in by the graph

    @property
    def citation(self) -> str:
        return f"[[{self.note.id}]] {self.note.path}:{self.line}"


class Retriever(Protocol):
    def search(self, query: str, now: dt.datetime, limit: int = 8) -> list[Hit]: ...


class BM25Retriever:
    """Built from notes plus their file text (for line-accurate citations). Disposable."""

    def __init__(self, notes: dict[str, Note], texts: dict[str, str]):
        self.notes = {i: n for i, n in notes.items() if not n.archived}
        self.texts = texts
        self.bags = {i: tokenize(" ".join([n.title] * TITLE_WEIGHT + list(n.tags) * TITLE_WEIGHT + [n.body]))
                     for i, n in self.notes.items()}
        self.tf = {i: Counter(b) for i, b in self.bags.items()}
        self.avgdl = (sum(len(b) for b in self.bags.values()) / len(self.bags)) if self.bags else 1.0
        df: Counter = Counter()
        for b in self.bags.values():
            df.update(set(b))
        n = len(self.bags)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.backlinks: dict[str, set[str]] = {}
        for i, note in self.notes.items():
            for target in note.links:
                self.backlinks.setdefault(target, set()).add(i)

    def _bm25(self, note_id: str, terms: list[str]) -> float:
        tf, dl = self.tf[note_id], len(self.bags[note_id])
        s = 0.0
        for t in terms:
            if t in tf:
                f = tf[t]
                s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avgdl))
        return s

    def _recency(self, note: Note, now: dt.datetime) -> float:
        stamp = note.updated or note.created
        if not stamp:
            return 0.5
        age = max(0.0, (now - parse_time(stamp)).total_seconds() / 86400)
        return 0.5 + 0.5 * 0.5 ** (age / HALF_LIFE_DAYS)   # never zero: old truths still count

    def _best_line(self, note: Note, terms: set[str]) -> int:
        text = self.texts.get(note.id, "")
        lines = text.splitlines()
        start = note.body_start_line(text) if text.startswith("---") else 1
        best, best_n = start, -1
        for i in range(start - 1, len(lines)):
            n = len(terms & set(tokenize(lines[i])))
            if n > best_n:
                best, best_n = i + 1, n
        return best

    def search(self, query: str, now: dt.datetime, limit: int = 8) -> list[Hit]:
        terms = tokenize(query)
        if not terms:
            return []
        scored: dict[str, tuple[float, str]] = {}
        for i, note in self.notes.items():
            if note.expired(now):
                continue
            s = self._bm25(i, terms)
            if s > 0:
                scored[i] = (s * self._recency(note, now) * TYPE_WEIGHT.get(note.type, 1.0), "match")
        # One hop of graph: what a hit links to, and what links to a hit.
        for i, (s, _) in sorted(scored.items()):
            neighbours = set(self.notes[i].links) | self.backlinks.get(i, set())
            for j in sorted(neighbours):
                if j in self.notes and not self.notes[j].expired(now):
                    s2 = s * LINK_DISCOUNT
                    if s2 > scored.get(j, (0.0, ""))[0]:
                        scored[j] = (s2, f"link:{i}")
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0]))[:limit]
        tset = set(terms)
        return [Hit(self.notes[i], round(s, 6), self._best_line(self.notes[i], tset), via)
                for i, (s, via) in ranked]
