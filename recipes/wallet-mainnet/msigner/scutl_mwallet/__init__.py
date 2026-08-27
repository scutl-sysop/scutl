"""scutl-mwallet: mainnet custody shell around the scutl_signer core.

Recipe #1 (wallet-mainnet, id mwallet). The inner signer owns keys, per-tx
and daily caps, idempotency, and the spend log; this package adds the
founding ceremony, the lifetime cap, human-ratcheted ceilings with a
cooling-off delay, the un-gated panic switch, and the phased sweep.
Gasless by construction: every spend is an EIP-3009 authorization the
counterparty (or, for sweep, the human) submits and pays gas for.
"""

from .core import Custodian

__all__ = ["Custodian"]
