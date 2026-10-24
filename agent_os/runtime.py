"""The runtime: the only way an agent touches the workspace.

Agents never get a file handle. They call `runtime.write(...)`, `runtime.act(...)`
or `runtime.run(...)`, and every call goes guard → gate → budget → do → feed.
Any failure in a check means the action does not happen, and the attempt is
logged as BLOCKED.
"""
from __future__ import annotations
import datetime as dt
import os

from .budget import Budget, BudgetExceeded
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

    def _month(self) -> str:
        return f"{self.clock():%Y-%m}"

    def write(self, agent: str, path: str, content: str, cost_usd: float = 0.0) -> str:
        decision, info = self.gate.evaluate(agent, "write", path)
        if decision != "allow":
            self.feed.log(agent, f"BLOCKED write {path} ({info})")
            raise Blocked(str(info))
        try:
            rel = self.guard.check(agent, path)
            if cost_usd:
                self.budget.charge(agent, cost_usd, self._month())
        except (ScopeViolation, BudgetExceeded) as e:
            self.feed.log(agent, f"BLOCKED write {path} ({e})")
            raise Blocked(str(e)) from e
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        self.feed.log(agent, "wrote file", artifact=rel)
        return rel

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
