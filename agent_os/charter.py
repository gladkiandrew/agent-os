"""Charters: one markdown file per agent. The charter is the single source of truth.

A charter's frontmatter declares what the agent is allowed to do. Anything not
declared is denied. The body (the agent's actual instructions) is free text and
is never parsed for permissions.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field

LIST_KEYS = {"write_scope", "allowed_actions"}


@dataclass(frozen=True)
class Charter:
    name: str
    role: str
    status: str = "pending"            # only "active" agents may run
    write_scope: tuple = ()            # glob patterns, relative to the workspace root
    allowed_actions: tuple = ()        # e.g. ("write", "draft", "outbound")
    monthly_budget_usd: float = 0.0
    body: str = field(default="", repr=False)

    @property
    def runnable(self) -> bool:
        return self.status == "active"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-subset parser: `key: value` and `key:` followed by `- item` lines.

    Deliberately tiny so the permission surface is easy to audit. Unknown syntax
    raises rather than guessing.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("charter must start with a '---' frontmatter block")
    meta: dict = {}
    current_list = None
    for i, raw in enumerate(lines[1:], start=1):
        line = raw.rstrip()
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:]).strip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- "):
            if current_list is None:
                raise ValueError(f"list item outside a list key on line {i + 1}")
            meta[current_list].append(line.lstrip()[2:].strip().strip('"').strip("'"))
            continue
        if ":" not in line:
            raise ValueError(f"cannot parse charter line {i + 1}: {line!r}")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value == "":
            meta[key] = []
            current_list = key
        else:
            meta[key] = value
            current_list = None
    raise ValueError("unterminated frontmatter block")


def charter_from_text(text: str) -> Charter:
    meta, body = parse_frontmatter(text)
    for k in ("name", "role"):
        if not meta.get(k):
            raise ValueError(f"charter is missing required key {k!r}")
    for k in LIST_KEYS:
        if k in meta and not isinstance(meta[k], list):
            raise ValueError(f"{k!r} must be a list")
    return Charter(
        name=meta["name"],
        role=meta["role"],
        status=meta.get("status", "pending"),
        write_scope=tuple(meta.get("write_scope", [])),
        allowed_actions=tuple(meta.get("allowed_actions", [])),
        monthly_budget_usd=float(meta.get("monthly_budget_usd", 0) or 0),
        body=body,
    )


def load_charters(directory: str) -> dict[str, Charter]:
    charters = {}
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".md"):
            with open(os.path.join(directory, fn), encoding="utf-8") as f:
                c = charter_from_text(f.read())
            if c.name in charters:
                raise ValueError(f"duplicate charter name {c.name!r}")
            charters[c.name] = c
    return charters
