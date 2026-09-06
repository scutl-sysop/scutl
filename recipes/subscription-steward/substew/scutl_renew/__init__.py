"""scutl_renew: the typed-tool component of recipe #14 (subscription-steward).

The agent keeps a subscription alive at the price the human consented
to. Every "hold the consented line" decision is made HERE, in code, from
the settled renewal total and the consent record: the model never gets
to judge that a hike is "worth it", an upward re-quote parks for fresh
human consent instead of settling, one settle per period makes
double-billing a hard-fail, and a cancel is done only when the log shows
billing stopped.

Rev 1 is bench-first: no live merchant is blessed yet, so MerchantClient
is the shape a blessed merchant must fit and the mock in scutbench/renew
implements the same surface. The manifest's contracts block is the
source of truth for ops and failure modes.
"""
