"""scutl_amail — the agent-email guardrail component (recipe #5, amail rev 1).

The model composes text; it never addresses envelopes. Allowlist,
ceilings, idempotency keys, the append-only mail log, and the
data-framing of inbound content all live here, in code.
"""

__all__ = ["core", "state", "provider", "cli"]
