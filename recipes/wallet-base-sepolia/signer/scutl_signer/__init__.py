"""scutl wallet signer — the guardrail component of recipe #1.

Manifest invariants this package enforces (recipe.yaml components.signer):
  - seed/private-key material is never returned, logged, or accepted as a
    tool argument;
  - spend caps are enforced inside pay(), in code;
  - the spend log is append-only and counters derive from it on restart.
"""

__version__ = "0.1.0"
