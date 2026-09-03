---
name: provision-vultr
description: Rent, operate, and destroy Vultr compute instances under code-enforced spend limits via the prov CLI. Use when asked to provision, list, or tear down infrastructure, manage DNS records in the delegated subzone, or check provisioning status/limits.
---

# Provision your own infrastructure (Vultr, SCUTL recipe #3 rev 1)

The `prov` CLI is the ONLY interface to the provider. All spend walls
(plan allowlist, region allowlist, hourly-price ceiling, instance
ceiling) are enforced inside `prov` before any API call leaves the box.
This spend is card-funded — no wallet cap governs it; these limits are
the only wall.

## Invocation

Use the absolute paths (venv console scripts; bare names through PATH
are the most common integration failure):

- `prov` -> {{PROV_BIN}} (see INSTALL.md; replace at install time)
- State dir: `${SCUTL_PROV_STATE:-~/.scutl/provision}`

## Tools (shell invocations)

| tool | command |
|---|---|
| infra_status | `prov status` |
| infra_create | `prov create --plan <plan> --region <region> --label <label>` |
| infra_list | `prov list` |
| infra_destroy | `prov destroy --id <instance_id>` (never gated; also `prov destroy-all`) |
| infra_dns | `prov dns set --name <n> --type <t> --value <v>` / `prov dns delete --name <n> --type <t>` / `prov dns list` |
| infra_admin | `prov admin configure ...` / `prov admin set-key --key-file <path>` / `prov admin decommission` (human-approved: exit 4 means ask the human to run `prov-approve <op>`, then retry once) |

omp exposes a generic bash tool, not per-tool gating; that degradation
is acceptable — the walls hold in `prov`'s own code.

## Exit-code taxonomy (the protocol — never collapse these)

0 success · 2 not-configured (run setup first) · 3 decommissioned
(create/dns refuse; destroy still works) · 4 approval-required (ask the
human to run `prov-approve <op>`, retry once) · 5 limit-refused (report
to the human verbatim; do NOT retry with different parameters — a
refusal is a limit working, not an obstacle) · 1 invalid (stop, show
the human the exact JSON error).

## Operating loop (verbatim from recipe.yaml execute.loop)

Provision on request, inside the limits: prov create, then poll
prov list until the instance is active and has an IP. Report the
instance id, plan, region, and IP verbatim from tool output. When the
workload is done, prov destroy — an instance nobody is using is spend,
not capacity. Periodically (or when asked): prov status; if it reports
foreign or unreconciled instances, report to the human — never destroy
what the log does not know.

## Provisioning inside the limits (verbatim from recipe.yaml smol.action.notes)

To rent an instance, run exactly:
`prov create --plan <plan> --region <region> --label <label>`

- Then poll `prov list` until status is active and copy the id,
  plan, region, and main_ip character-for-character from the JSON
  into your report. Never write N/A or a placeholder; if a value is
  missing, paste the entire JSON line and say it was missing.
- A refusal with exit 5 is a limit working, not an obstacle: report
  it verbatim and stop. Never retry with a different plan, region,
  or label to get around a refusal.
- Create exactly once. If the create looks failed, slow, or odd,
  run `prov list` BEFORE any retry — a pending instance is a
  success in flight, and a second create is a second bill. Your
  final report's instance count comes from a fresh `prov list`,
  never from memory.
- When the workload is done, run `prov destroy --id <id>` — an idle
  instance is spend. Destroy never needs approval.
- Never call the provider API directly and never edit the state
  files; prov is the only interface.

## Additional guardrails

- Never remember instance ids, IPs, counts, or limits from earlier in
  the conversation; re-run `prov status` / `prov list`.
- The API key lives only in the state dir at 0600. It is never
  printed, echoed, put in argv or an env var, or copied into your
  context or error reports. `set-key` consumes a FILE path only.
- Never automate `prov-approve` token minting — it is the human's.
- Do not wrap destroy in extra confirmation ceremony: on any
  instruction to stop / shut down / tear it down, run
  `prov destroy-all` FIRST, ask questions after. It needs no approval
  and works even when everything else refuses. Decommission
  (`prov admin decommission`, human-approved) comes only after
  live_instances is 0.
