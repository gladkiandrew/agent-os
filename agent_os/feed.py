"""Activity feed: one markdown file per day, one line per action.

    - 14:05 · **Writer** — drafted weekly post — [[Content/drafts/post.md]]

The contract is simple: if there is no feed line, it did not happen. Blocked
actions are logged too, so the feed shows what agents tried, not only what
they managed.
"""
from __future__ import annotations
import datetime as dt
import os


class ActivityFeed:
    def __init__(self, root: str, clock=None, folder: str = "Agents/Logs"):
        self.root, self.folder = root, folder
        self.clock = clock or dt.datetime.now

    def _path(self, when: dt.datetime) -> str:
        return os.path.join(self.root, self.folder, f"{when:%Y-%m-%d}.md")

    def log(self, agent: str, action: str, artifact: str | None = None, retro: dict | None = None) -> str:
        when = self.clock()
        path = self._path(when)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        line = f"- {when:%H:%M} · **{agent}** — {action}"
        if artifact:
            line += f" — [[{artifact}]]"
        lines = [line]
        if retro:
            for k in ("worked", "failed", "next"):
                if retro.get(k):
                    lines.append(f"    - {k}: {retro[k]}")
        with open(path, "a", encoding="utf-8") as f:
            if new:
                f.write(f"---\ntype: agent-log\ndate: {when:%Y-%m-%d}\n---\n\n")
            f.write("\n".join(lines) + "\n")
        return line

    def read(self, day: dt.date) -> list[str]:
        path = os.path.join(self.root, self.folder, f"{day:%Y-%m-%d}.md")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f if l.startswith("- ")]
