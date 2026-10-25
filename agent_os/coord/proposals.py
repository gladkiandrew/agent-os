"""Proposals: the only way shared memory or a charter changes.

A proposal pins an exact path and the SHA-256 of the exact content to be written.
Approval goes through the existing ApprovalQueue, and the runtime refuses any write
to a protected path unless it matches an approved, unused proposal byte for byte.
So approving "promote this insight" can never be stretched into "and also widen my
write scope".
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..charter import parse_frontmatter
from ..gate import ApprovalQueue, Request
from ..scope import glob_match

HUMAN = "human"
PROTECTED = ("Memory/Shared/**", "Agents/*.md")
CHARTER_GLOB = "Agents/*.md"


class ProposalRefused(PermissionError):
    pass


def is_protected(rel: str) -> bool:
    return any(glob_match(rel, g) for g in PROTECTED)


def sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class Proposal:
    request: Request
    proposer: str
    path: str
    content: str
    digest: str
    reason: str
    applied: bool = False
    decided_by: str = ""

    @property
    def id(self) -> int:
        return self.request.id

    @property
    def kind(self) -> str:
        return "charter" if glob_match(self.path, CHARTER_GLOB) else "memory"


class ProposalBook:
    """Files proposals in the ApprovalQueue and remembers what each one may write."""

    def __init__(self, queue: ApprovalQueue, charters: dict, read_text=None):
        self.queue, self.charters = queue, charters
        self.read_text = read_text or (lambda rel: None)
        self.by_id: dict[int, Proposal] = {}

    def propose(self, proposer: str, path: str, content: str, reason: str) -> Proposal:
        if not is_protected(path):
            raise ProposalRefused(f"{path!r} is not a protected path; write it directly")
        if glob_match(path, CHARTER_GLOB):
            # A charter proposal may change the body (instructions, validated insights)
            # but never the frontmatter, which is where permissions live.
            current = self.read_text(path)
            if current is None:
                raise ProposalRefused(f"no charter at {path!r} to amend")
            if parse_frontmatter(current)[0] != parse_frontmatter(content)[0]:
                raise ProposalRefused("a charter proposal may not change frontmatter (permissions)")
        digest = sha(content)
        req = self.queue.submit(proposer, "propose", f"{path} @{digest[:12]}: {reason}")
        p = Proposal(req, proposer, path, content, digest, reason)
        self.by_id[p.id] = p
        return p

    def decide(self, proposal_id: int, approve: bool, reviewer: str, note: str = "") -> Proposal:
        p = self.by_id[proposal_id]
        if reviewer == p.proposer:
            raise ProposalRefused("nobody approves their own proposal")
        if reviewer != HUMAN:
            if p.kind == "charter":
                raise ProposalRefused("charter changes need a human")
            c = self.charters.get(reviewer)
            if c is None or not c.runnable or "review" not in c.allowed_actions:
                raise ProposalRefused(f"{reviewer} is not a chartered reviewer")
        self.queue.decide(proposal_id, approve, note)
        p.decided_by = reviewer
        return p

    def authorize(self, path: str, content: str) -> Proposal | None:
        digest = sha(content)
        for p in self.by_id.values():
            if (p.path == path and p.digest == digest and not p.applied
                    and p.request.status == "approved"):
                return p
        return None

    def pending(self) -> list[Proposal]:
        return [p for p in self.by_id.values() if p.request.status == "pending"]
