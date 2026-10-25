"""A memory note: markdown with frontmatter, written so a human can read it in any editor.

The note format IS the database format. Every field an index needs is in the
frontmatter, and every relationship is a `[[link]]` in the body, so deleting every
index loses nothing: they rebuild from the files.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..charter import parse_frontmatter

TYPES = ("fact", "decision", "learning", "entity", "episode", "handoff")

# Higher wins. A write may never replace a note with a lower-confidence version, so an
# agent's inference can't quietly overwrite something a person actually said.
CONFIDENCE = {"inferred": 0, "observed": 1, "stated": 2}

SOURCE_KINDS = ("agent:", "human:", "external:")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
TIME_FMT = "%Y-%m-%dT%H:%M"


class NoteError(ValueError):
    pass


def fmt_time(t: dt.datetime) -> str:
    return t.strftime(TIME_FMT)


def parse_time(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, TIME_FMT)


def slugify(text: str, max_len: int = 48) -> str:
    """Readable ids: whole words only, so an id never ends mid-word."""
    out = ""
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        nxt = f"{out}-{word}" if out else word
        if len(nxt) > max_len:
            break
        out = nxt
    return out or "note"


@dataclass
class Note:
    id: str
    type: str
    title: str
    body: str
    source: str
    confidence: str = "observed"
    tags: tuple = ()
    created: str = ""
    updated: str = ""
    expires_at: str = ""
    subject: str = ""          # decisions only: what the decision is about
    stance: str = ""           # decisions only: what was decided
    archived: str = ""         # set by Runtime.archive; archived notes are not recalled
    path: str = field(default="", compare=False)

    # ---- validation ----
    def validate(self) -> None:
        if not ID_RE.match(self.id):
            raise NoteError(f"bad note id {self.id!r}: use lowercase letters, digits and dashes")
        if self.type not in TYPES:
            raise NoteError(f"unknown note type {self.type!r}; expected one of {TYPES}")
        if not self.title.strip():
            raise NoteError(f"note {self.id} has no title")
        if not self.source.strip():
            raise NoteError(f"note {self.id} has no source — provenance is mandatory")
        if not self.source.startswith(SOURCE_KINDS):
            raise NoteError(f"note {self.id} source {self.source!r} must start with one of {SOURCE_KINDS}")
        if self.confidence not in CONFIDENCE:
            raise NoteError(f"note {self.id} confidence {self.confidence!r} not in {tuple(CONFIDENCE)}")
        if self.confidence == "stated" and not self.source.startswith("human:"):
            raise NoteError(f"note {self.id}: only a human source can make a note 'stated'")
        if self.type == "decision" and not (self.subject and self.stance):
            raise NoteError(f"decision {self.id} needs both subject and stance")

    # ---- derived ----
    @property
    def links(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(m.group(1).strip() for m in LINK_RE.finditer(self.body)))

    @property
    def is_external(self) -> bool:
        return self.source.startswith("external:") or "external" in self.tags

    def expired(self, now: dt.datetime) -> bool:
        return bool(self.expires_at) and parse_time(self.expires_at) <= now

    # ---- (de)serialization ----
    def to_markdown(self) -> str:
        lines = ["---", f"id: {self.id}", f"type: {self.type}", f"title: {self.title}",
                 f"source: {self.source}", f"confidence: {self.confidence}"]
        if self.subject:
            lines.append(f"subject: {self.subject}")
        if self.stance:
            lines.append(f"stance: {self.stance}")
        lines.append("tags:")
        lines += [f"  - {t}" for t in self.tags]
        lines += [f"created: {self.created}", f"updated: {self.updated}"]
        if self.expires_at:
            lines.append(f"expires_at: {self.expires_at}")
        lines.append("---")
        return "\n".join(lines) + "\n\n" + self.body.rstrip() + "\n"

    @classmethod
    def from_markdown(cls, text: str, path: str = "") -> "Note":
        meta, body = parse_frontmatter(text)
        tags = meta.get("tags", [])
        return cls(id=meta.get("id", ""), type=meta.get("type", ""), title=meta.get("title", ""),
                   body=body, source=meta.get("source", ""),
                   confidence=meta.get("confidence", "observed"),
                   tags=tuple(tags) if isinstance(tags, list) else (),
                   created=meta.get("created", ""), updated=meta.get("updated", ""),
                   expires_at=meta.get("expires_at", ""), subject=meta.get("subject", ""),
                   stance=meta.get("stance", ""), archived=meta.get("archived", ""), path=path)

    def body_start_line(self, text: str) -> int:
        """1-based file line where the body starts, so citations point at real lines."""
        lines = text.splitlines()
        closing = next(i for i, l in enumerate(lines[1:], start=1) if l.strip() == "---")
        i = closing + 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        return i + 1
