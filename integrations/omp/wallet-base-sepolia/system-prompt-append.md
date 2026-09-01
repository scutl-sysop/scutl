# Wallet guardrails (SCUTL wallet-base-sepolia, carried verbatim from recipe.yaml)

Purchase loop: On a 402 response: call wallet_buy with the resource URL
and a fresh payment id (derive it from the task, e.g. task-slug-1). It
reads the offer, cap-checks, pays, confirms the settle tx on-chain, and
records the spend in one call.

Over-cap: exit 5 (cap-exceeded); agent reports the offer and asks the human.
Transient: exit 6; retry with the SAME payment id (idempotent, never double-pays).

Guardrails:
- One payment per offer; retries must reuse the same payment id (idempotent).
- Never place key material or approval tokens in tool arguments or logs.
