"""Public-side probes for the paid-service public-tls leaf (cst-8ih.7).

Run FROM A BOX THAT IS NEITHER MERCHANT NOR ACCEPTANCE BUYER, against the
public endpoint. Mechanizes the manifest's public-tls verify checks:

  1. offer_public          unpaid GET https://{host}/resource -> 402 offer
                           with the configured price and payTo
  2. cert_valid            TLS chain validates for the hostname (plus days
                           to expiry recorded)
  3. plaintext_downgrade   http:// redirects to https and never serves
  4. containment           pserv's loopback bind port is NOT reachable on
                           the public address
  5. underpay_refused      authorization below price -> 402 "underpayment"
                           (refused in-code, before the facilitator)
  6. payto_injection_safe  a payTo smuggled into the authorization neither
                           serves with a bogus signature nor changes the
                           advertised payTo
  7. replay_refused        (only with --replay-header FILE, the exact
                           X-PAYMENT of an already-settled purchase —
                           buyer.json's x_payment field) -> 402 "replayed"

    python public_probes.py https://pserv.scutl.org \
        --payto 0x... --price 0.01 [--bind-port 8402] \
        [--replay-header buyer_header.txt] [--out public.json]

Exit 0 iff all graded checks pass; JSON verdict on stdout (and --out).
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import ssl
import sys
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import requests

RESOURCE = "/resource"


def _fake_header(value: str, payto: str, extra: dict | None = None) -> str:
    """A syntactically valid x402 header with a garbage signature.

    Underpay must be refused by the merchant IN CODE before any facilitator
    call, so the signature is never examined; the injection probe's bogus
    signature must fail facilitator verification and therefore never serve.
    """
    auth = {"nonce": "0x" + "5c" * 32, "value": value,
            "from": "0x" + "bb" * 20, "to": payto, **(extra or {})}
    payload = {"x402Version": 1, "scheme": "exact", "network": "base-sepolia",
               "payload": {"signature": "0x" + "cd" * 65,
                           "authorization": auth}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--payto", required=True)
    ap.add_argument("--price", required=True, help="USDC, e.g. 0.01")
    ap.add_argument("--bind-port", type=int, default=8402)
    ap.add_argument("--replay-header",
                    help="file holding the X-PAYMENT of a settled purchase")
    ap.add_argument("--out")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    host = urlparse(base).hostname
    url = base + RESOURCE
    price_units = str(int(Decimal(args.price).scaleb(6)))
    checks: dict[str, object] = {}

    # 1. unpaid 402 offer, correct from OUTSIDE (verify=True end to end)
    offer = None
    try:
        r = requests.get(url, timeout=30)
        offer = r.json()["accepts"][0] if r.status_code == 402 else None
        checks["offer_public"] = (
            True if offer and offer["payTo"] == args.payto
            and offer["maxAmountRequired"] == price_units
            and offer["network"] == "base-sepolia"
            else f"status {r.status_code}, offer {offer}")
    except Exception as e:  # noqa: BLE001 — any failure is the finding
        checks["offer_public"] = f"error: {e}"

    # 2. certificate sanity: requests already validated chain+hostname above;
    #    record issuer/expiry independently so the receipt shows its work.
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        not_after = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (not_after - datetime.now(timezone.utc)).days
        checks["cert_valid"] = (True if days > 7
                                else f"expires in {days}d ({not_after})")
        checks["cert_days_left"] = days
    except Exception as e:  # noqa: BLE001
        checks["cert_valid"] = f"error: {e}"

    # 3. plaintext downgrade: http redirects, never serves
    try:
        r80 = requests.get(f"http://{host}{RESOURCE}",
                           timeout=30, allow_redirects=False)
        loc = r80.headers.get("Location", "")
        checks["plaintext_downgrade"] = (
            True if r80.status_code in (301, 302, 307, 308)
            and loc.startswith("https://")
            else f"status {r80.status_code}, location {loc!r}")
    except Exception as e:  # noqa: BLE001
        checks["plaintext_downgrade"] = f"error: {e}"

    # 4. containment: the rev-1 loopback bind must be an internal detail
    try:
        with socket.create_connection((host, args.bind_port), timeout=8):
            checks["containment"] = (
                f"port {args.bind_port} REACHABLE from outside")
    except OSError:
        checks["containment"] = True

    # 5. underpay from the public side (in-code refusal precedes facilitator)
    try:
        r = requests.get(url, timeout=30,
                         headers={"X-PAYMENT": _fake_header("1", args.payto)})
        checks["underpay_refused"] = (
            True if r.status_code == 402 and "underpayment" in r.text
            else f"status {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        checks["underpay_refused"] = f"error: {e}"

    # 6. payto injection: full price, smuggled payTo, bogus signature.
    #    Must not serve (facilitator rejects the signature), must not 500,
    #    and the advertised payTo afterwards is byte-identical.
    try:
        evil = _fake_header(price_units, args.payto,
                            extra={"payTo": "0x" + "ee" * 20})
        # the merchant retries facilitator verify with backoff (0/15/45s)
        # before refusing a bogus signature — give it room
        r = requests.get(url, timeout=180, headers={"X-PAYMENT": evil})
        after = requests.get(url, timeout=30).json()["accepts"][0]
        checks["payto_injection_safe"] = (
            True if r.status_code == 402 and after["payTo"] == args.payto
            else f"status {r.status_code}, payTo after {after.get('payTo')}")
    except Exception as e:  # noqa: BLE001
        checks["payto_injection_safe"] = f"error: {e}"

    # 7. replay of a REAL settled authorization, if the harness provides it
    if args.replay_header:
        try:
            hdr = open(args.replay_header).read().strip()
            r = requests.get(url, timeout=30, headers={"X-PAYMENT": hdr})
            checks["replay_refused"] = (
                True if r.status_code == 402 and "replay" in r.text
                else f"status {r.status_code}: {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            checks["replay_refused"] = f"error: {e}"
    else:
        checks["replay_refused"] = "no replay header (not graded)"

    graded = {k: v for k, v in checks.items()
              if k != "cert_days_left"
              and v != "no replay header (not graded)"}
    green = all(v is True for v in graded.values())
    out = json.dumps({"green": green, "endpoint": url, "checks": checks},
                     indent=2)
    print(out)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out + "\n")
    sys.exit(0 if green else 1)


if __name__ == "__main__":
    main()
