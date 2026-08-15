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
import statistics
from pathlib import Path


def _rep_duration(rep_dir: Path) -> tuple[int | None, str | None]:
    """Wall-clock seconds for one rep, and how we know (cst-8ih.10).

    Exact from timing.json (run-rep.sh stamps, spans the driver run).
    Backfill for pre-stamp rungs: approximate from evidence mtimes —
    earliest harness-created log to transcript.txt. state/ is excluded:
    it is cp -a'd from the snapshot and keeps the snapshot's mtimes.
    """
    tpath = rep_dir / "timing.json"
    if tpath.exists():
        return json.loads(tpath.read_text())["duration_s"], "stamped"
    starts = [p.stat().st_mtime for p in
              (rep_dir / "server.log", rep_dir / "expected.json",
               rep_dir / "buyer.log")
              if p.exists()]
    end = rep_dir / "transcript.txt"
    if not starts or not end.exists():
        return None, None
    return max(0, round(end.stat().st_mtime - min(starts))), "mtime-approx"


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
    timing_sources: set[str] = set()
    for rep_dir in sorted(args.rung_dir.glob("rep-*")):
        gpath = rep_dir / "grade.json"
        if not gpath.exists():
            continue
        g = json.loads(gpath.read_text())
        duration, source = _rep_duration(rep_dir)
        reps.append({"rep": rep_dir.name, "green": g["green"],
                     "duration_s": duration,
                     "tx": g.get("tx"), "checks": g["checks"]})
        if source:
            timing_sources.add(source)

    n_green = sum(r["green"] for r in reps)
    durations = [r["duration_s"] for r in reps if r["duration_s"] is not None]
    # Owner direction (cst-8ih.10): wall time is the accessibility story —
    # median goes in the protocol block, not buried in per-rep detail.
    wall_time = {
        "median_rep_s": round(statistics.median(durations)) if durations else None,
        "total_s": sum(durations) if durations else None,
        "reps_timed": len(durations),
        "source": ("mixed" if len(timing_sources) > 1
                   else next(iter(timing_sources)) if timing_sources else None),
    }
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
                                else "yellow" if n_green else "red",
                     "wall_time": wall_time},
        "cost": args.cost,
        "interventions": 0,   # bump manually if a human touched a rep
        "notes": args.note,
        "reps": reps,
    }
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
