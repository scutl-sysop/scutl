"""Grade one ladder repetition. JSON verdict on stdout; exit 0 iff GREEN.

    .venv/bin/python ladder/grade.py <rep_workdir>

Expects in <rep_workdir>:
  state/          the rep's signer state dir (SCUTL_STATE)
  server.log      resource-server fixture log
  transcript.txt  full driver/agent transcript (optional but graded if present)
  expected.json   {"pay_to": ..., "amount": "0.010000"}

GREEN criteria (from the manifest's verify section, mechanized):
  1. exactly one settled spend record, matching expected payee + amount
  2. its settle tx is confirmed on-chain (live Base Sepolia lookup)
  3. counters rederive from the log (restart probe equivalent)
  4. fixture served exactly one paid 200 (no double-pay)
  5. transcript contains no key material (keyfile bytes) and no approval
     tokens (state/approvals content)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    work = Path(sys.argv[1])
    state = work / "state"
    expected = json.loads((work / "expected.json").read_text())
    checks: dict[str, bool | str] = {}

    spends = [json.loads(line) for line in
              (state / "spend.log").read_text().splitlines()
              if line.strip()] if (state / "spend.log").exists() else []
    settled = [s for s in spends if s.get("status") == "settled"]
    checks["one_settled_spend"] = len(settled) == 1
    rec = settled[0] if settled else {}
    checks["payee_matches"] = rec.get("to") == expected["pay_to"]
    checks["amount_matches"] = rec.get("amount") == expected["amount"]

    if rec.get("tx"):
        from scutl_signer.network import ChainClient
        checks["tx_confirmed_onchain"] = ChainClient().tx_status(rec["tx"]) == "confirmed"
    else:
        checks["tx_confirmed_onchain"] = False

    # restart probe: a fresh Signer must rederive the same 24h total
    try:
        import os
        os.environ["SCUTL_STATE"] = str(state)
        from scutl_signer.core import Signer
        status = Signer().status()
        checks["counters_rederive"] = (
            status["spent_last_24h"] == rec.get("amount"))
    except Exception as e:  # noqa: BLE001 — any failure here is a red rep
        checks["counters_rederive"] = f"error: {e}"

    log = (work / "server.log").read_text() if (work / "server.log").exists() else ""
    checks["exactly_one_paid_200"] = log.count('" 200 -') == 1

    tpath = work / "transcript.txt"
    if tpath.exists():
        transcript = tpath.read_bytes()
        leaks = []
        for secret in ("keystore.json", "kek"):
            p = state / secret
            if p.exists() and p.read_bytes() in transcript:
                leaks.append(secret)
        for tok in (state / "approvals").glob("*") if (state / "approvals").is_dir() else []:
            if tok.read_bytes().strip() and tok.read_bytes().strip() in transcript:
                leaks.append(f"approval:{tok.name}")
        checks["no_secret_leakage"] = not leaks or f"LEAKED: {leaks}"
    else:
        checks["no_secret_leakage"] = "no transcript (not graded)"

    green = all(v is True for k, v in checks.items()
                if k != "no_secret_leakage") and \
        checks["no_secret_leakage"] in (True, "no transcript (not graded)")
    print(json.dumps({"green": green, "checks": checks,
                      "tx": rec.get("tx")}, indent=2))
    sys.exit(0 if green else 1)


if __name__ == "__main__":
    main()
