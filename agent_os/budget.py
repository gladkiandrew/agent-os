"""Per-agent monthly budget caps. A charge that would cross the cap is refused whole."""
from __future__ import annotations
from collections import defaultdict


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, charters: dict):
        self.caps = {n: c.monthly_budget_usd for n, c in charters.items()}
        self.spent = defaultdict(float)

    def charge(self, agent: str, usd: float, month: str) -> float:
        if usd < 0:
            raise ValueError("charges are non-negative")
        cap = self.caps.get(agent, 0.0)
        key = (agent, month)
        if self.spent[key] + usd > cap + 1e-9:
            raise BudgetExceeded(f"{agent}: ${self.spent[key] + usd:.2f} would exceed the ${cap:.2f} cap for {month}")
        self.spent[key] += usd
        return cap - self.spent[key]
