# Installing paid-service-x402 into omp (v17.2.12)

Verified against omp 17.2.12: omp discovers Agent Skills-standard
`SKILL.md` files from `~/.agents/skills/<name>/` (user) and
`.agents/skills/<name>/` (project walk-up), gated by the
`skills.enableAgentsUser` / `skills.enableAgentsProject` settings
(default on). There is no per-tool gating for shell commands — the
skill exposes pserv via omp's `bash` tool; guardrails hold in pserv's
code (acceptable degradation per ADAPT.md Step 2).

## 1. Install pserv

    python3 -m venv ~/.local/share/pserv-venv          # or your convention
    ~/.local/share/pserv-venv/bin/pip install <recipe-dir>/pserv
    ~/.local/share/pserv-venv/bin/pserv status         # expect exit 2, JSON "not-configured"

Console scripts installed: `pserv`, `pserv-approve` (the human's
approval-token minter). State dir:
`${SCUTL_PSERV_STATE:-~/.scutl/paid-service}`.

## 2. Install the skill

    mkdir -p ~/.agents/skills/paid-service-x402
    cp SKILL.md ~/.agents/skills/paid-service-x402/SKILL.md
    # then replace the __PSERV_VENV_BIN__ placeholder with the real
    # venv bin dir, e.g.:
    sed -i 's|__PSERV_VENV_BIN__|/home/you/.local/share/pserv-venv/bin|' \
        ~/.agents/skills/paid-service-x402/SKILL.md

(Project-scoped alternative: `.agents/skills/paid-service-x402/SKILL.md`
in the repo.)

## 3. Carry the guardrails into system context

Either rely on the skill text alone, or additionally:

    omp --append-system-prompt @system-prompt.md ...

or place the system-prompt.md content in the project's AGENTS.md /
`.agents/SYSTEM.md` so every session carries it.

## 4. Setup with the human (not automated)

Follow recipe.yaml `setup:` in order, inside an omp session:

1. `pserv status` → exit 2 (healthy pre-configure).
2. `pserv admin configure --payto <ADDR> --price <USDC> --offering <...>
   --bind-port <PORT>` → first attempt exits 4 (that IS the prompt).
   The payTo address comes from the human's own typed input; read it
   back to them, have them run `pserv-approve configure` against the
   same state dir, retry once.
3. `pserv start`; `pserv status`; unpaid `curl` → 402.
4. public-tls leaf only: dns-record and install-proxy are actor:human
   (Caddy, DNS, firewall) — print instructions, run only the `verify:`
   curls.

## 5. Acceptance

Run recipe.yaml `verify:` checks from inside an omp session. The paid
probe needs a buyer wallet (wallet rev 1 / x402-v2 recipe) and the
human's go-ahead; report totals and settle tx hashes digit-for-digit.
