"""Consolidation: the weekly review that makes memory compound instead of pile up.

Four passes, all deterministic (same vault in, same report out):

  1. decay     — episodes nobody ever cited, and feed days, older than the horizon are
                 moved to Archive/. Logs decay; insights compound.
  2. merge     — learnings that say the same thing (normalized-token Jaccard) collapse
                 into the oldest one; the rest are archived with `merged into [[id]]`,
                 and their citations follow the survivor.
  3. promote   — a learning cited by enough later work is proposed into its owner's
                 charter under "## Validated insights". A PROPOSAL: a human decides.
  4. flag      — two live decisions on the same subject with different stances are
                 reported for a human. The review never picks a winner itself.

"Cited" means a live note from a DIFFERENT run links to it: a run linking its own
output proves nothing. Citations are counted from the files every time, so the
count can't drift from what the vault actually says. Duplicates are judged on the
title, which states the claim; bodies carry each run's own evidence and differ by design.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field

from ..runtime import Runtime
from .note import Note, parse_time
from .search import tokenize
from .store import SHARED, MemoryStore

INSIGHTS = "## Validated insights"
MERGED_RE = re.compile(r"merged into \[\[([^\]]+)\]\]")


@dataclass
class Report:
    date: str
    archived: list[tuple[str, str]] = field(default_factory=list)          # (path, reason)
    merged: list[tuple[str, str]] = field(default_factory=list)            # (duplicate, survivor)
    proposals: list[tuple[int, str, str]] = field(default_factory=list)    # (id, charter, note id)
    contradictions: list[tuple[str, list]] = field(default_factory=list)   # (subject, [(id, stance, source)])
    path: str = ""

    def to_markdown(self) -> str:
        out = ["---", "type: consolidation-report", f"date: {self.date}", "---", "",
               f"# Consolidation — {self.date}", "", "## Archived (decay)", ""]
        out += [f"- {p} — {why}" for p, why in self.archived] or ["- nothing past the horizon"]
        out += ["", "## Merged duplicates", ""]
        out += [f"- [[{d}]] → [[{s}]]" for d, s in self.merged] or ["- none"]
        out += ["", "## Promotion proposals (need a human)", ""]
        out += [f"- #{pid} [[{nid}]] into {path}" for pid, path, nid in self.proposals] or ["- none"]
        out += ["", "## Contradictions (need a human)", ""]
        for subject, sides in self.contradictions:
            out.append(f"- **{subject}**: " + " vs ".join(f"[[{i}]] says {s!r} ({src})" for i, s, src in sides))
        if not self.contradictions:
            out.append("- none")
        return "\n".join(out) + "\n"


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def aliases(store: MemoryStore) -> dict[str, str]:
    """duplicate id → survivor id, read back from the archive stamps."""
    out = {}
    for nid, n in store.load(include_archived=True).items():
        m = MERGED_RE.search(n.archived or "")
        if m:
            out[nid] = m.group(1)
    return out


def citation_counts(notes: dict[str, Note], alias: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for nid, n in notes.items():
        for target in set(alias.get(t, t) for t in n.links):
            cited = notes.get(target)
            if target == nid or (cited is not None and cited.source == n.source):
                continue
            counts[target] = counts.get(target, 0) + 1
    return counts


def _owner(note: Note) -> str:
    return note.path.split("/")[1]


def _with_insight(charter_text: str, line: str) -> str:
    text = charter_text.rstrip("\n")
    if INSIGHTS not in text:
        return f"{text}\n\n{INSIGHTS}\n\n{line}\n"
    head, _, tail = text.partition(INSIGHTS)
    nxt = tail.find("\n## ")
    section, rest = (tail, "") if nxt < 0 else (tail[:nxt], tail[nxt:])
    return f"{head}{INSIGHTS}{section.rstrip()}\n{line}\n{rest}"


def consolidate(store: MemoryStore, steward: str, decay_days: int = 14,
                merge_threshold: float = 0.8, promote_after: int = 3) -> Report:
    rt: Runtime = store.rt
    now = rt.clock()
    horizon = now - dt.timedelta(days=decay_days)
    report = Report(date=f"{now:%Y-%m-%d}")
    rt.feed.log(steward, "consolidation started")

    # 1. decay
    notes = store.load()
    cited = citation_counts(notes, aliases(store))
    for nid in sorted(notes):
        n = notes[nid]
        if n.type == "episode" and parse_time(n.created) < horizon and not cited.get(nid):
            why = f"decayed: uncited episode older than {decay_days}d"
            rt.archive(steward, n.path, why)
            report.archived.append((n.path, why))
    logs = os.path.join(rt.root, rt.feed.folder)
    for fn in sorted(os.listdir(logs)) if os.path.isdir(logs) else []:
        try:
            day = dt.datetime.strptime(fn[:-3], "%Y-%m-%d")
        except ValueError:
            continue
        if day < horizon.replace(hour=0, minute=0):
            rel = f"{rt.feed.folder}/{fn}"
            why = f"decayed: feed older than {decay_days}d"
            rt.archive(steward, rel, why)
            report.archived.append((rel, why))

    # 2. merge duplicate learnings: oldest survives
    notes = store.load()
    learnings = sorted((n for n in notes.values() if n.type == "learning"),
                       key=lambda n: (n.created, n.id))
    survivors: list[tuple[Note, set]] = []
    for n in learnings:
        bag = set(tokenize(n.title))
        match = next((s for s, sbag in survivors if jaccard(bag, sbag) >= merge_threshold), None)
        if match is None:
            survivors.append((n, bag))
            continue
        rt.archive(steward, n.path, f"merged into [[{match.id}]]")
        report.merged.append((n.id, match.id))
    if report.merged:
        rt.feed.log(steward, "merged duplicate learnings: " + ", ".join(f"{d} → {s}" for d, s in report.merged))

    # 3. promote well-cited learnings into the owner's charter — as proposals
    notes = store.load()
    cited = citation_counts(notes, aliases(store))
    for nid in sorted(notes):
        n = notes[nid]
        if n.type != "learning" or cited.get(nid, 0) < promote_after or _owner(n) == SHARED:
            continue
        path = f"Agents/{_owner(n)}.md"
        current = rt.read(path)
        if current is None or f"[[{nid}]]" in current:
            continue
        if any(p.path == path and p.request.status == "pending" for p in rt.proposals.by_id.values()):
            continue
        line = f"- {n.title} — [[{nid}]] (cited {cited[nid]}×, promoted {report.date})"
        p = rt.proposals.propose(steward, path, _with_insight(current, line),
                                 f"promote [[{nid}]] (cited {cited[nid]}×)")
        rt.feed.log(steward, f"proposed #{p.id}: promote {nid} into {path} (cited {cited[nid]}×)")
        report.proposals.append((p.id, path, nid))

    # 4. contradictions between live decisions
    by_subject: dict[str, list[Note]] = {}
    for n in sorted(notes.values(), key=lambda n: n.id):
        if n.type == "decision" and not n.expired(now):
            by_subject.setdefault(n.subject.strip().lower(), []).append(n)
    for subject in sorted(by_subject):
        group = by_subject[subject]
        if len({" ".join(tokenize(n.stance)) for n in group}) > 1:
            sides = [(n.id, n.stance, n.source) for n in group]
            report.contradictions.append((subject, sides))
            rt.feed.log(steward, f"FLAG contradiction on {subject!r}: " +
                        " vs ".join(f"{i} ({s})" for i, s, _ in sides))

    report.path = rt.write(steward, f"Reviews/Consolidation/{report.date}.md", report.to_markdown())
    rt.feed.log(steward, "consolidation complete", retro={
        "worked": f"{len(report.archived)} archived, {len(report.merged)} merged, "
                  f"{len(report.proposals)} proposed, {len(report.contradictions)} flagged",
        "failed": "none",
        "next": "a human decides the proposals and contradictions"})
    return report
