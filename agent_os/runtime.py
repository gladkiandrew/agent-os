"""The runtime: the only way an agent touches the workspace.

Agents never get a file handle. They call `runtime.write(...)`, `runtime.act(...)`
or `runtime.run(...)`, and every call goes guard → gate → budget → do → feed.
Any failure in a check means the action does not happen, and the attempt is
logged as BLOCKED.

Protected paths (shared memory, charters) add one more check on top: the exact
content must match an approved proposal. Nothing here bypasses the guard; the
second-brain layers (memory, board, bus) are all callers of these methods.
"""
from __future__ import annotations
import datetime as dt
import os

from .budget import Budget, BudgetExceeded
from .coord.proposals import ProposalBook, is_protected
from .feed import ActivityFeed
from .gate import ActionGate, ApprovalQueue
from .scope import ScopeViolation, WriteScopeGuard


class Blocked(PermissionError):
    pass


class Runtime:
    def __init__(self, charters: dict, root: str, clock=None):
        self.charters, self.root = charters, root
        self.clock = clock or dt.datetime.now
        self.guard = WriteScopeGuard(charters, root)
        self.queue = ApprovalQueue()
        self.gate = ActionGate(charters, self.queue)
        self.budget = Budget(charters)
        self.feed = ActivityFeed(root, clock=self.clock)
        self.proposals = ProposalBook(self.queue, charters, read_text=self.read)

    def _month(self) -> str:
        return f"{self.clock():%Y-%m}"

    def write(self, agent: str, path: str, content: str, cost_usd: float = 0.0,
              summary: str | None = None) -> str:
        decision, info = self.gate.evaluate(agent, "write", path)
        if decision != "allow":
            self.feed.log(agent, f"BLOCKED write {path} ({info})")
            raise Blocked(str(info))
        try:
            rel = self.guard.check(agent, path)
            proposal = None
            if is_protected(rel):
                proposal = self.proposals.authorize(rel, content)
                if proposal is None:
                    raise ScopeViolation(f"{rel!r} is protected: it changes only by an approved proposal")
            if cost_usd:
                self.budget.charge(agent, cost_usd, self._month())
        except (ScopeViolation, BudgetExceeded) as e:
            self.feed.log(agent, f"BLOCKED write {path} ({e})")
            raise Blocked(str(e)) from e
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        if proposal is not None:
            proposal.applied = True
            self.feed.log(agent, f"applied proposal #{proposal.id}", artifact=rel)
        else:
            self.feed.log(agent, summary or "wrote file", artifact=rel)
        return rel

    def read(self, path: str) -> str | None:
        """Reading is unrestricted inside the workspace; escaping it is not."""
        rel = self.guard.normalize(path)
        full = os.path.join(self.root, rel)
        if not os.path.isfile(full):
            return None
        with open(full, encoding="utf-8") as f:
            return f.read()

    def append(self, agent: str, path: str, text: str) -> str:
        """Append-only write (inboxes, ledgers): same gate and guard as `write`."""
        decision, info = self.gate.evaluate(agent, "write", path)
        if decision != "allow":
            self.feed.log(agent, f"BLOCKED append {path} ({info})")
            raise Blocked(str(info))
        try:
            rel = self.guard.check(agent, path)
            if is_protected(rel):
                raise ScopeViolation(f"{rel!r} is protected: it changes only by an approved proposal")
        except ScopeViolation as e:
            self.feed.log(agent, f"BLOCKED append {path} ({e})")
            raise Blocked(str(e)) from e
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "a", encoding="utf-8") as f:
            f.write(text)
        return rel

    def archive(self, agent: str, path: str, reason: str) -> str:
        """Move a note to Archive/<same path>. Never deletes, never edits the body.

        The reason is stamped into the frontmatter (`archived:`) so the archive
        explains itself and any index can be rebuilt from the files alone.
        """
        try:
            rel = self.guard.check_archive(agent, path)
        except ScopeViolation as e:
            self.feed.log(agent, f"BLOCKED archive {path} ({e})")
            raise Blocked(str(e)) from e
        src = os.path.join(self.root, rel)
        if not os.path.isfile(src):
            raise Blocked(f"nothing to archive at {rel!r}")
        dest_rel = "Archive/" + rel
        dest = os.path.join(self.root, dest_rel)
        with open(src, encoding="utf-8") as f:
            text = f.read()
        if text.startswith("---\n"):
            text = f"---\narchived: {reason}\n" + text[4:]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        os.remove(src)
        self.feed.log(agent, f"archived ({reason})", artifact=dest_rel)
        return dest_rel

    def act(self, agent: str, action: str, detail: str):
        """Non-file actions (outbound, spend, publish...). Returns the approval request if one was filed."""
        decision, info = self.gate.evaluate(agent, action, detail)
        if decision == "deny":
            self.feed.log(agent, f"BLOCKED {action}: {detail} ({info})")
            raise Blocked(str(info))
        if decision == "needs_human":
            self.feed.log(agent, f"requested approval #{info.id} for {action}: {detail}")
            return info
        self.feed.log(agent, f"{action}: {detail}")
        return None

    def run(self, agent: str, task, retro: bool = True):
        """Run a task callable(runtime, agent). Always ends with a retro line, even on failure."""
        charter = self.charters.get(agent)
        if charter is None or not charter.runnable:
            self.feed.log(agent, "BLOCKED run (no active charter)")
            raise Blocked(f"{agent} has no active charter")
        try:
            result = task(self, agent)
        except Blocked as e:
            if retro:
                self.feed.log(agent, "run ended blocked", retro={"worked": "-", "failed": str(e), "next": "needs a human"})
            raise
        if retro:
            summary = result if isinstance(result, dict) else {}
            self.feed.log(agent, "run complete", retro={
                "worked": summary.get("worked", "completed"),
                "failed": summary.get("failed", "none"),
                "next": summary.get("next", "-"),
            })
        return result
