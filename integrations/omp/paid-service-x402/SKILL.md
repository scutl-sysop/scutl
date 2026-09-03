---
name: paid-service-x402
description: Operate the pserv x402 paid-service daemon — check status, start/stop it, and report income truthfully from earnings.log. Use when the human asks about the paid service, its health, its earnings, or asks to start, stop, configure, or decommission it.
---

# paid-service-x402 (pserv) — operator skill

You operate a merchant daemon that SELLS a resource for x402 payments.
The safety model is walls-in-code, not walls-in-prompt: the daemon
refuses to serve before the facilitator verifies AND settles; `payTo`
is fixed in config and nothing you pass can move it; `earnings.log` is
append-only and is the ONLY source income may be quoted from.

## Invocation

omp has no per-tool gating for these commands; invoke them via the
`bash` tool. This is an acceptable degradation — the guardrails are
enforced in pserv's code, not by tool wiring.

All commands live in the install venv. Use absolute paths (bare names
through PATH are the most common integration failure):

    PSERV=__PSERV_VENV_BIN__/pserv

State dir: `${SCUTL_PSERV_STATE:-~/.scutl/paid-service}` — export
`SCUTL_PSERV_STATE` first if a non-default state dir is in use.

## Tools (names and arity are the protocol)

| tool             | command                          |
|------------------|----------------------------------|
| service_status   | `$PSERV status`                  |
| service_start    | `$PSERV start`                   |
| service_stop     | `$PSERV stop`                    |
| service_earnings | `$PSERV earnings --since <iso>` (`--since` optional) |
| service_admin    | `$PSERV admin <op>` (human-gated; see below) |

`service_admin` ops — each requires a token the HUMAN mints with
`pserv-approve <op>` (same state dir); enforced in code:

- `configure`: `$PSERV admin configure --payto <addr> --price <usdc> --offering <static-file|generated-text> [--bind-addr <ip>] --bind-port <port> [--resource-path <path>]`
- `set-payto`: `$PSERV admin set-payto --payto <addr>` — moves money's
  destination. NEVER automate its token; the address must come from the
  human's own typed input, read back to them before approval.
- `decommission`: `$PSERV admin decommission` — stops the daemon and
  makes start refuse forever.

## Exit-code taxonomy (surface these; never collapse them)

- 0 success
- 2 not-configured — run setup first
- 3 decommissioned — stop; tell the human; never restart
- 4 approval-required — ask the human to run `pserv-approve <op>`, retry once
- 1 invalid — stop, show the human the exact JSON error

## Operating loop (verbatim from recipe.yaml execute.loop)

Periodically (or when asked): service_status. If the daemon is down and
not decommissioned: service_start, then verify with service_status. When
reporting income: service_earnings, and quote totals ONLY from it —
copy the total and settle tx verbatim into the report (the digits
themselves must appear; narrating that they were reported does not
count) — never estimate earnings from memory of requests seen.

If it crashes: service_start once; if it dies again within a minute,
stop and report to the human — do not flap.

## Guardrails

- Never edit config.json, earnings.log, or served.log directly; the
  typed tools are the only interface.
- Never restart a decommissioned service; refusal is exit 3, report it.
- A buyer complaint of "paid but not served" is answered from
  earnings.log + served.log evidence, not from apology or re-serve;
  re-serve without a matching settle is an over-cap-equivalent refusal.
- Never modify proxy config, firewall rules, DNS, or certificates —
  those are human-provisioned infrastructure (the caddy-ingress
  component has deliberately NO agent-facing tools; do not create any).
- Never remember earnings totals, service health, or config from
  earlier in the conversation — re-run `pserv status` / `pserv earnings`.
- Never write N/A, None, or a placeholder where a value belongs; if a
  value is missing from the output, paste the entire JSON line and say
  it was missing.

## Emergency

If the human says stop / shut down / decommission the service, run
`$PSERV admin decommission`. It needs the human's approval token
(exit 4 → ask them to run `pserv-approve decommission`, retry once).
Make no other decisions; config and logs are retained for
reconciliation.
