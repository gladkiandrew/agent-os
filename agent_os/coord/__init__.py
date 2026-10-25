"""Coordination: the task board, leases, handoffs, the message bus and proposals.

Kept import-free on purpose: `runtime` depends on `coord.proposals`, and the board
depends on `runtime`, so this package must not import its own modules eagerly.
"""
