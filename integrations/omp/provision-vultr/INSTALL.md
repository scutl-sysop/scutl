# Installing provision-vultr into omp (v17.2.12)

omp discovers Agent Skills-standard SKILL.md files from
`~/.agents/skills/<name>/SKILL.md` (user-wide) and from
`.agents/skills/` / `.agent/skills/` walking up from the project dir.
Verified against omp 17.2.12 (`--no-skills` / `--skills` flags;
changelog: "Added support for loading skills from ~/.agents/skills").

## Steps (human or agent with approval)

1. Install the CLI into a stable venv (NOT a scratch dir):
   ```bash
   python3 -m venv ~/.venvs/scutl-prov
   ~/.venvs/scutl-prov/bin/pip install \
     /home/star/seats/star/work/scutl/recipes/provision-vultr/prov
   ```
   Console scripts: `~/.venvs/scutl-prov/bin/prov` and
   `~/.venvs/scutl-prov/bin/prov-approve`.

2. Copy the skill and pin the binary path:
   ```bash
   mkdir -p ~/.agents/skills/provision-vultr
   cp -f SKILL.md ~/.agents/skills/provision-vultr/SKILL.md
   sed -i 's|{{PROV_BIN}}|/home/star/.venvs/scutl-prov/bin/prov|' \
     ~/.agents/skills/provision-vultr/SKILL.md
   ```
   (omp sessions get PATH from the login shell; the absolute path in
   the skill removes the venv-on-PATH failure mode.)

3. Optional: for sessions without skills discovery, append
   `system-prompt.md` via `omp --append-system-prompt @system-prompt.md`.

4. Continue with recipe.yaml `setup:` — human steps (Vultr account,
   API key file), then `prov admin configure` / `set-key` with
   `prov-approve` tokens minted by the human.

## Verification status of this integration (2026-09-03, sandboxed)

Done in a scratch state dir (SCUTL_PROV_STATE set; ~/.scutl/provision
never touched), no Vultr API calls, no approval tokens minted:

- pip install of ./prov into a fresh venv: OK; `prov` and
  `prov-approve` scripts present.
- `prov status` unconfigured: exit 0, JSON
  `{"configured": false, "key_present": false, "decommissioned": false}`.
  NOTE: ADAPT.md and recipe.yaml both say to expect exit 2 here; the
  code exits 2 only for ops that need config (`list`, `create`).
  Manifest/code disagreement — reported as a doc bug.
- `prov list` / `prov create` unconfigured: exit 2, not-configured. OK.
- `prov admin configure` without token: exit 4 approval-required with
  the prov-approve prompt; no state written. OK.
- `prov destroy --id fake`: exit 5, "not log-known-live; foreign
  instances are never touched". OK (destroy reachable ungated).

NOT done (needs human / live account): configure+set-key (approval
tokens), probe-auth, DNS delegation, and the Step 5 acceptance cycle
(create/destroy costs real money). Acceptance is therefore
blocked-on-harness-setup and blocked-on-human; nothing above is an
acceptance run.
