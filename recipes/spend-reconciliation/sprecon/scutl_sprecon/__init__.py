"""scutl spend-reconciliation component (recipe sprc rev 1, cst-r0vz).

The reconciler moves no money and holds no credentials: it reads the
agent's append-only books, discovers chain transfers, joins provider
statements fetched by the recipe that owns the key, and appends
findings. Every residue is explained by a named honest category or
escalated with the joined evidence verbatim.
"""

from .core import Reconciler, ApprovalRequired

__all__ = ["Reconciler", "ApprovalRequired"]
