"""Write-scope guard. Fail-closed: anything not explicitly allowed is denied.

Denied cases, all of them on purpose:
  - an agent with no charter, or a charter that is not active
  - an absolute path, a path that escapes the workspace (`..`), or a symlink trick
  - a path that matches none of the agent's write_scope globs
"""
from __future__ import annotations
import fnmatch
import os
import posixpath


class ScopeViolation(PermissionError):
    pass


def glob_match(path: str, pattern: str) -> bool:
    """Segment-aware glob: `*` never crosses a `/`; `**` matches zero or more segments."""
    p, g = path.split("/"), pattern.split("/")

    def m(i, j):
        if j == len(g):
            return i == len(p)
        if g[j] == "**":
            return any(m(k, j + 1) for k in range(i, len(p) + 1))
        return i < len(p) and fnmatch.fnmatchcase(p[i], g[j]) and m(i + 1, j + 1)
    return m(0, 0)


class WriteScopeGuard:
    def __init__(self, charters: dict, root: str):
        self.charters = charters
        self.root = os.path.realpath(root)

    def normalize(self, path: str) -> str:
        if not path or os.path.isabs(path) or path.startswith("~"):
            raise ScopeViolation(f"path must be workspace-relative: {path!r}")
        rel = posixpath.normpath(path.replace("\\", "/"))
        if rel == "." or rel.startswith("../") or rel == "..":
            raise ScopeViolation(f"path escapes the workspace: {path!r}")
        real = os.path.realpath(os.path.join(self.root, rel))
        if os.path.commonpath([self.root, real]) != self.root:
            raise ScopeViolation(f"path resolves outside the workspace: {path!r}")
        return rel

    def check(self, agent: str, path: str) -> str:
        charter = self.charters.get(agent)
        if charter is None:
            raise ScopeViolation(f"unknown agent {agent!r} — no charter, no writes")
        if not charter.runnable:
            raise ScopeViolation(f"{agent} is {charter.status!r}, not active")
        rel = self.normalize(path)
        for pattern in charter.write_scope:
            if glob_match(rel, pattern):
                return rel
        raise ScopeViolation(f"{agent} may not write {rel!r} (scope: {list(charter.write_scope)})")
