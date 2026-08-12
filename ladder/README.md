# Ladder run harness (cst-8ih.1 → receipts cst-8ih.3)

Runs the emitted skill bundles against the target ladder and grades each
repetition machine-readably. The receipt record is built from these
grades plus the pinned environment.

## Rungs

| rung      | bundle              | model                          | serving                     | verdict rule |
|-----------|---------------------|--------------------------------|-----------------------------|--------------|
| frontier  | `standard`          | Fable/Opus seat                | n/a (already run, green)    | debugs recipe |
| reference | `standard`          | Qwen 3.6 27B Q4_K_M            | llama-server on rented GPU  | MUST be green |
| headline  | `smol`              | 9B dense / a3b MoE (pick at run time) | llama-server on rented GPU | may be yellow; failures = lowering bugs |

- GPU: RunPod, single 4090-class (decision: Conway, 2026-08-12 — one
  provider, price delta vs marketplace not worth the cognitive overhead).
- Model artifact (reference): `unsloth/Qwen3.6-27B-GGUF`,
  `Qwen3.6-27B-Q4_K_M.gguf` (~16.8 GB). Pin the file sha256 in the receipt.
- Driver: Hermes (the modal local-harness user config), pointed at the
  pod's OpenAI-compatible endpoint, system context = the emitted
  `SKILL.md`, task = the standard purchase errand. The driver is
  pluggable via `LADDER_DRIVER` (any command that takes the rep workdir
  and runs the agent to completion) so the harness itself is
  harness-agnostic.

## Protocol (per rung)

- N = 15 repetitions. Setup (keygen + approval + backup + faucet funding)
  runs ONCE per rung and its state dir is snapshotted; each rep starts
  from a copy of the snapshot — fresh, isolated state without 15 faucet
  requests. The repeated measure is the Execute loop.
- Each rep: local resource server fixture (live x402.org facilitator,
  real Base Sepolia settle) at 0.01 USDC ⇒ a full 15-rep rung spends
  0.15 USDC + one setup purchase. Daily cap 1.00 covers two rungs.
- GREEN rep: `grade.py` exit 0 — exactly one settled spend, tx confirmed
  on-chain, log/counters consistent, no forbidden content in transcript.
- Rung verdict: green = 15/15 (reference MUST hit this to ship);
  headline reports the split and every failure is triaged as a lowering
  bug before any re-run.

## Environment pinning (goes into the receipt verbatim)

`pod-setup.sh` prints an `env.json` with: GPU model, driver, llama.cpp
version + build hash, model file + sha256, context length, sampler
settings, harness (name + version), bundle `bundle.json` (which pins the
manifest sha256). Reproducible by construction — a receipt whose env
block cannot be replayed is not a receipt.

## Files

- `pod-setup.sh` — run ON the pod: installs pinned llama.cpp, downloads
  the model, starts `llama-server`, writes `env.json`.
- `run-rep.sh` — run from the controller: one repetition (fresh state,
  fixture up, driver in, grade out).
- `grade.py` — machine verdict for one rep; JSON on stdout, exit 0 green.
