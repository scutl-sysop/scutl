"""Assemble a run receipt from a completed rung (cst-8ih.3).

    .venv/bin/python tools/receipt.py <rung_dir> \
        --bundle <bundle_dir> --env <env.json> --target reference \
        [--note ...] > receipts/<recipe>/<rev>/<target>.json

A receipt is the trust artifact of the registry: one rung of the target
ladder, machine-graded, environment pinned. It contains nothing that
cannot be re-derived from the rung's on-disk evidence (grade.json per
rep) plus the pinned environment — no prose claims.

Inputs:
  rung_dir     contains rep-*/grade.json (one per repetition)
  --bundle     the emitted bundle dir (bundle.json pins recipe/rev/
               profile/config/manifest sha)
  --env        env.json from pod-setup.sh (GPU, server build, model sha)
  --harness    e.g. "hermes-agent v0.20.0 (2026.8.3) @ 3c27eb6"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("rung_dir", type=Path)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--env", type=Path, required=True)
    p.add_argument("--target", required=True,
                   choices=("frontier", "reference", "headline", "experimental"))
    p.add_argument("--harness", required=True)
    p.add_argument("--date", required=True, help="run date, YYYY-MM-DD")
    p.add_argument("--cost", default=None,
                   help="e.g. '0.15 USDC testnet + ~$1.10 GPU'")
    p.add_argument("--note", action="append", default=[])
    args = p.parse_args()

    bundle = json.loads((args.bundle / "bundle.json").read_text())
    reps = []
    for rep_dir in sorted(args.rung_dir.glob("rep-*")):
        gpath = rep_dir / "grade.json"
        if not gpath.exists():
            continue
        g = json.loads(gpath.read_text())
        reps.append({"rep": rep_dir.name, "green": g["green"],
                     "tx": g.get("tx"), "checks": g["checks"]})

    n_green = sum(r["green"] for r in reps)
    receipt = {
        "receipt_format": 1,
        "recipe": bundle["recipe"],
        "rev": bundle["rev"],
        "target": args.target,
        "profile": bundle["profile"],
        "configuration": bundle["configuration"],
        "parameters": bundle["parameters"],
        "manifest_sha256": bundle["manifest_sha256"],
        "environment": json.loads(args.env.read_text()),
        "harness": args.harness,
        "date": args.date,
        "protocol": {"reps": len(reps), "green": n_green,
                     "verdict": "green" if reps and n_green == len(reps)
                                else "yellow" if n_green else "red"},
        "cost": args.cost,
        "interventions": 0,   # bump manually if a human touched a rep
        "notes": args.note,
        "reps": reps,
    }
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
