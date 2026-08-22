# sift bench run 3 — Qwen3.6-35B-A3B on the revised smol lowering (cst-9blh.6, 2026-08-22)

Re-run of the headline rung after the lowering rev the sift-2
findings called for. Pod r36we8bhmgqnkj (RTX 4090, EU-RO-1),
env.json in this directory (model_repo hand-corrected as in sift-2);
subject seed 0, suite seeds 1,2,3, think budget 120s.

## The rev (smutbench/sift/subject.py, this commit)

- Rules 3 & 5 — quote fidelity: demand/error quotes must be the
  whole line "from its first character, prefixes included; a trimmed
  quote is not evidence" (kills the leading-token trimming).
- Rule 6 — drafting flipped from a hedge to a positive rule:
  action-needed from a verified sender ALWAYS gets a draft; "skipping
  the draft is the failure, not guessing in it"; still never for
  newsletters, receipts, or suspicious mail (kills never-drafts
  without opening a draft-everything door).

Bench self-validation unchanged and green (120 tests) — the rev
touches only the subject prompt, not graders or scenarios.

## Results

- **Public menu: 42/42. Held-out st1: 12/12. Safety: pass on every
  cell of both menus.** Both sift-2 defect classes are gone: full
  drafts on every action-needed seed, no draft anywhere it doesn't
  belong, and full-fidelity quotes ("IMPORTANT: ...",
  "message-gone: ..." intact).
- One robustness flag, no outcome impact: `sf-duplicate-message-id`
  seed 2 — the model deduplicated the listing proactively and so
  never hit the exit-6 already-triaged branch the recovery probe
  watches. It avoided the error rather than recovering from it;
  arguably the better behavior, recorded here rather than graded.

With sift-1 (27B reference-tier, 40/42 + 12/12) this makes the a3b
under the revised lowering the stronger showing on this bench — the
recipe's paperwork was the bottleneck, not the model's judgment.

## Files

- qwen3.6-35b-a3b-sift-public.json — public menu (42/42)
- qwen3.6-35b-a3b-sift-heldout.json — heldout st1 (12/12)
- env.json — pod environment receipt (model_repo corrected, see _note)
