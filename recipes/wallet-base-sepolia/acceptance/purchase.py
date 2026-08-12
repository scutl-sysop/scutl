"""Acceptance driver: buy an x402-priced resource with the signer.

Usage: .venv/bin/python acceptance/purchase.py <url> <payment-id>

The flow now lives in the shipped component (scutl_signer.buy, exposed as
the `x402-buy` entry point) so the emitted skill and this acceptance run
exercise the exact same code path.
"""

import sys

from scutl_signer.buy import main

if __name__ == "__main__":
    url, payment_id = sys.argv[1], sys.argv[2]
    main([url, "--payment-id", payment_id])
