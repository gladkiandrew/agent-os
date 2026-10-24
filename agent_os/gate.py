"""Action gate: some actions are never automatic, whatever the charter says.

Spending money, sending anything outbound, publishing, deploying and touching
secrets always go to a human approval queue. A charter can allow an agent to
*request* them; it cannot allow an agent to *do* them.
"""
from __future__ import annotations
import itertools
from dataclasses import dataclass, field

HUMAN_REQUIRED = frozenset({"spend", "outbound", "publish", "deploy", "secrets"})


@dataclass
class Request:
    id: int
    agent: str
    action: str
    detail: str
    status: str = "pending"            # pending | approved | denied
    note: str = ""


@dataclass
class ApprovalQueue:
    requests: dict = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)

    def submit(self, agent: str, action: str, detail: str) -> Request:
        r = Request(next(self._ids), agent, action, detail)
        self.requests[r.id] = r
        return r

    def decide(self, request_id: int, approve: bool, note: str = "") -> Request:
        r = self.requests[request_id]
        if r.status != "pending":
            raise ValueError(f"request {request_id} already {r.status}")
        r.status, r.note = ("approved" if approve else "denied"), note
        return r

    def pending(self) -> list:
        return [r for r in self.requests.values() if r.status == "pending"]


class ActionGate:
    def __init__(self, charters: dict, queue: ApprovalQueue):
        self.charters, self.queue = charters, queue

    def evaluate(self, agent: str, action: str, detail: str = "") -> tuple[str, object]:
        """Return ("allow", None), ("needs_human", Request) or ("deny", reason)."""
        charter = self.charters.get(agent)
        if charter is None or not charter.runnable:
            return "deny", f"{agent} has no active charter"
        if action not in charter.allowed_actions:
            return "deny", f"{agent} is not chartered for {action!r}"
        if action in HUMAN_REQUIRED:
            return "needs_human", self.queue.submit(agent, action, detail)
        return "allow", None
