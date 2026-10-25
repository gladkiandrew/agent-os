"""agent-os: a second brain for an AI-native operation, on a fail-closed governance layer.

Every agent action passes through three checks before it touches anything:
the write-scope guard (where may it write), the action gate (does a human need
to approve this), and the budget (can it afford it). Every action that happens
lands a line in the activity feed. No feed line means it did not happen.

On top of that sit typed memory that compounds (`agent_os.memory`) and coordination
so several agents can share it (`agent_os.coord`). Both are callers of the runtime,
never alternatives to it. `Brain` wires them together; `run_task` is one agent run.
"""
from .charter import Charter, load_charters
from .scope import WriteScopeGuard, ScopeViolation
from .gate import ActionGate, ApprovalQueue, HUMAN_REQUIRED
from .feed import ActivityFeed
from .quarantine import quarantine, Quarantined
from .budget import Budget, BudgetExceeded
from .runtime import Runtime, Blocked
from .brain import Brain
from .loop import run_task

__all__ = ["Charter", "load_charters", "WriteScopeGuard", "ScopeViolation",
           "ActionGate", "ApprovalQueue", "HUMAN_REQUIRED", "ActivityFeed",
           "quarantine", "Quarantined", "Budget", "BudgetExceeded", "Runtime", "Blocked",
           "Brain", "run_task"]
__version__ = "0.2.0"
