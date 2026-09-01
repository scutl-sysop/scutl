# Installing wallet-base-sepolia into OMP (v17.2.12)

Files here and where they go live. Nothing in this directory touches a
live config; a human (or an agent with authority) performs the copy
steps below.

## What's here

- `skills/wallet-base-sepolia/SKILL.md` — the OMP skill (Agent Skills
  standard: SKILL.md with YAML frontmatter). Carries the tool table
  (wallet_status/pay/buy/sign/admin), the exit-code taxonomy, and the
  recipe's `execute.loop` + `execute.guardrails` verbatim.
- `system-prompt-append.md` — the guardrail text alone, for harness
  configurations that inject system-prompt text instead of (or in
  addition to) skills: `omp --append-system-prompt @system-prompt-append.md`.

## Where it goes live, and why

OMP discovers skills (Agent Skills standard) from, among others:

- `~/.agents/skills/` — OMP-native user-level, canonical (setting
  `skills.enableAgentsUser`, default on)
- `.agents/skills/` in the project, walking up from cwd to repo root
  (`skills.enableAgentsProject`, default on)
- `~/.claude/skills/`, `.claude/skills/`, `~/.codex/skills/`, and pi
  dirs (compat toggles, default on)
- extra dirs via `skills.customDirectories` in
  `~/.omp/agent/settings.json`

Recommended: copy `skills/wallet-base-sepolia/` into
`~/.agents/skills/` (wallet identity is per-user, not per-project):

```bash
mkdir -p ~/.agents/skills
cp -rf skills/wallet-base-sepolia ~/.agents/skills/
```

Zero-copy alternative (no dotfile writes): add this directory as a
custom skill dir, or use `--config` overlay, e.g. set
`skills.customDirectories` to include
`<this-dir>/skills`.

## Signer install (per user)

```bash
python3 -m venv ~/.scutl/venv
~/.scutl/venv/bin/pip install <recipe-dir>/signer
~/.scutl/venv/bin/signer status   # expect exit 2 (not-setup) before keygen
```

Then edit `SKILL.md`'s `<VENV>` placeholder to the real venv path —
absolute paths, per the recipe's PATH warning.

## Tool mapping note (fidelity caveat)

OMP's first-class custom tools are TypeScript extension modules
(`omp help` → "Plugin Options"; docs at
https://omp.sh/docs/extension-authoring and docs/custom-tools.md;
auto-discovered as `tools/<name>/index.ts`, or via `.mcp.json`). This
integration instead uses the ADAPT.md-sanctioned "documented shell
invocations" convention: the SKILL.md exposes the five named tools as
distinct documented commands run through OMP's `bash` tool. The
recipe's per-tool policy gating (e.g. gating `wallet_admin` behind
OMP's approval prompts specifically) is therefore enforced only by the
signer's own in-code approval-token gate — which is the recipe's actual
wall — plus skill text. A future rev could lift the five tools into a
TypeScript extension (or an MCP server) to let OMP's
`tools.approvalMode` policy distinguish `wallet_admin` from the rest.

## Ceremony (human-in-the-loop, after install)

Per recipe.yaml `setup:`: keygen (agent runs, human mints approval
token with `signer-approve keygen`), backup (human copies keyfile
offline; agent verifies with `signer admin backup-verify`), fund
(faucet; Base Sepolia network only). Then run the five `verify:`
acceptance checks from inside an OMP session.
