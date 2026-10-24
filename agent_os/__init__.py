"""agent-os: a small, fail-closed governance layer for a crew of AI agents.

Every agent action passes through three checks before it touches anything:
the write-scope guard (where may it write), the action gate (does a human need
to approve this), and the budget (can it afford it). Every action that happens
lands a line in the activity feed. No feed line means it did not happen.
"""
from .charter import Charter, load_charters
from .scope import WriteScopeGuard, ScopeViolation
from .gate import ActionGate, ApprovalQueue, HUMAN_REQUIRED
from .feed import ActivityFeed
from .quarantine import quarantine, Quarantined
from .budget import Budget, BudgetExceeded
from .runtime import Runtime, Blocked

__all__ = ["Charter", "load_charters", "WriteScopeGuard", "ScopeViolation",
           "ActionGate", "ApprovalQueue", "HUMAN_REQUIRED", "ActivityFeed",
           "quarantine", "Quarantined", "Budget", "BudgetExceeded", "Runtime", "Blocked"]
__version__ = "0.1.0"
