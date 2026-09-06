"""scutl_pwatch: the typed-tool component of recipe #25 (price-watch-and-buy).

The agent watches the price of a named item and buys when — and only
when — the fully-loaded settled total holds at or below a target the
human set. Every "hold the line" decision is made HERE, in code, from
the settled total: the model never gets to judge that a price is "good
enough", and no merchant message (a personalized sticker, a fake anchor,
a scarcity countdown) can move the target or the caps.

Rev 1 is bench-first: no live merchant is blessed yet, so MerchantClient
is the shape a blessed merchant must fit and the mock in scutbench/pwatch
implements the same surface. The manifest's contracts block is the
source of truth for ops and failure modes.
"""
