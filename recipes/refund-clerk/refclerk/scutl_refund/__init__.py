"""scutl_refund: the typed-tool component of recipe #15 (refund-clerk).

The agent works a queue of refund claims against an agent-run paid
service. Every "is this refund real" decision is made HERE, in code,
from the read-only earnings.log, the append-only refunds.log, and the
human-set policy: a refund settles only against a settle the ledger
proves, once per settle, for at most the settled amount, to the address
that paid — the payout address is never a parameter, so nothing a
claimant writes can move money anywhere else. A verified claim outside
policy PARKS for a human exception; an unproven claim is refused with
evidence. Parking is for "true but big"; refusal is for "unproven".

Rev 1 is bench-first: no live claim source or payout rail is blessed,
so ClaimsClient/SettlementClient are the shapes blessed rails must fit
and the mocks in smutbench/refund implement the same surfaces. The
manifest's contracts block is the source of truth for ops and failure
modes.
"""
