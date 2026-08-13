# SCUTL Recipe Manifest Schema — draft 1 (cst-8ih.2)

Extracted by example from `recipes/wallet-base-sepolia/recipe.yaml`.
Normative words (MUST/SHOULD/MAY) are used in the RFC sense. The schema is
deliberately small: a section earns its place only if recipe #1 needed it.

A manifest is one YAML document with these top-level keys, all required
unless marked optional:

```
recipe · decide · parameters? · components · bindings · contracts
setup · execute · verify · recover · migrate? · targets
```

## Design invariants (apply to every section)

1. **Every reachable Decide leaf is a blessed, tested configuration.**
   Untested choices MUST NOT appear as selectable options; they are recorded
   in `deferred-options` (metadata only, invisible to the installer flow).
2. **Provider specifics live only in `bindings`.** The rest of the manifest
   refers to dependencies by contract role (`facilitator`, `faucet`, ...).
   A new provider or network is a rev of `bindings` alone — and re-runs the
   receipt ladder.
3. **Contracts are mock-derivable.** `contracts` MUST state each dependency's
   operations and failure modes precisely enough that SMUTbench (cst-8ih.4)
   can generate a mock service with the same behavioral contract, with no
   reference to `bindings`.
4. **Safety lives in components, not prose.** Any guarantee the recipe
   claims (caps, secret confinement) MUST be enforced in shipped component
   code and listed under that component's `invariants`. Steps and skills may
   *repeat* an invariant; they may not be its only enforcement.
5. **Two distinct human relationships** (see `actor` vs `approval` below).

## Sections

### `recipe`
Identity block: `id` (slug), `rev` (integer; any change to blessed behavior
bumps it), `title`, `summary`, `status` (`draft` → `reference-green` →
`shipped`; a recipe MUST NOT ship without a green reference receipt),
`maintainer` (always `first-party` in the curated distro).

### `decide`
Ordered list of installer questions. Each entry:
- `id`, `question`
- `options[]`: each with `id`, `label`, `blessed: true` (always true — see
  invariant 1), and optionally `asks:` (parameter ids this choice requires).
- `deferred-options` (optional): ids we considered and have not blessed.

The cross-product of chosen options is the *configuration*; every
configuration reachable through the tree MUST have a receipt before
`status: shipped`.

### `parameters` (optional)
Typed values a Decide option asks for: `type`, `prompt`, `default`.
Types are recipe-domain scalars (`decimal-usdc`, `string`, ...); the schema
does not define arithmetic — components consume and enforce them.

### `components`
Code the recipe ships. Each component:
- `kind`: `typed-tool` (MCP/typed boundary; a component MUST NOT be a
  shell script the model composes freely) or `ingress-service` (added by
  paid-service rev 2: human-provisioned infrastructure such as a reverse
  proxy — it exposes NO agent-facing tools, and its `invariants` are
  custody statements, e.g. who holds TLS key material).
- `source`: path within the recipe.
- `state_dir`: where the component owns durable state. Component state is
  the single source of truth for anything safety-relevant; the model's
  context never is.
- `tools[]`: the typed operations exposed.
- `invariants[]`: the guarantees enforced in this component's code. These
  are audit targets: a reviewer should be able to point at the lines that
  make each one true. SMUTbench safety scenarios are generated from them.

### `bindings`
- `live`: one entry per contract role, with provider identity and connection
  specifics. This is the ONLY place provider names/URLs/chain-ids appear.
- `stub` (optional): declared-but-refusing slots proving the layering
  (e.g. `base-mainnet` in recipe #1). A stub MUST fail loudly if selected.

### `contracts`
One entry per external dependency role:
- `ops[]`: signatures in `name(args) -> result | error(kind)` prose form.
- `failure_modes[]`: enumerated, kebab-case. This list is the SMUTbench
  scenario menu; adding a failure mode here adds benchmark coverage
  automatically.

### `setup`, `execute`, `verify`, `recover`
The four phases lowered into the emitted skill.

- `setup`: ordered steps. Each step has `run` (agent instruction) or
  `instructions` (human instruction), plus optionally `verify` and
  `fallback` (`trigger:` names contract failure modes; the fallback body is
  a blessed alternate path, not an improvisation license). A step MAY carry
  `when: {decide_id: option_id, ...}` — it is lowered only into
  configurations where every named question resolved to that option
  (added by paid-service rev 2; naming an unknown decide id is a lowering
  error, not a silent skip). Parameter slots in step prose (`run`,
  `instructions`, `expect`, `verify`) are filled at lowering time, same as
  in `commands`.
- `execute`: `loop` (the steady-state behavior), boundary behaviors
  (e.g. `over_cap`), and `guardrails[]` (restatements of component
  invariants plus behavioral rules like idempotent retry).
- `verify`: acceptance checks. The recipe is not installed until all pass.
  MUST include at least: one end-to-end exercise of the real flow, one
  negative probe per safety invariant (e.g. over-cap refusal), and one
  restart/resume probe. A check MAY be `{check: "...", when: {...}}` to
  gate it to a configuration, with the same `when:` semantics as setup.
- `recover`: named procedures (`restore`, `revoke`, ...). Emergency
  procedures SHOULD carry zero decisions beyond invoking them (ratified
  principle, cst-8ih epic comment 2026-08-11).

**Actor vs approval** (any step or procedure):
- `actor: human` — the human PERFORMS the step; the agent at most verifies
  the result. Used when the action itself must not pass through the agent
  (moving key material).
- `approval: human` — the component performs the operation, gated on an
  out-of-band human approval the model cannot fabricate. Used for
  irreversible admin ops (keygen, revoke).
Default when neither appears: the agent acts autonomously within component
invariants. Approval friction is NOT a safety mechanism (see epic
principle); reserve `approval:` for genuinely irreversible operations.

### `migrate` (optional; added by paid-service rev 2)
Revision mechanics, present from rev 2 of any recipe onward. A rev is
superseded, never invalidated: receipts are keyed by
(recipe, rev, manifest_sha) and STAND when a new rev lands. Fields:
- `from-rev`: the rev this block migrates from.
- `identity`: when an old-rev deployment already IS a valid configuration
  of the new rev (e.g. a renamed Decide leaf), say so — nothing to run.
- one prose entry per migration path (e.g. `to-public-tls`): the Decide
  re-answer plus the delta setup steps, and what state carries across.

### `targets`
The lowering ladder (design: cst-8ih.1). Roles are fixed:
- `frontier` — debugs the recipe itself.
- `reference` — modal user config; MUST be green before `shipped`.
- `headline` — smol profile; the honest test of lowering discipline. MAY be
  yellow; each failure is triaged as a lowering bug.
Each target: `model-class`, optional `harness`, optional `profile`.

## Open questions (carried, not blocking)

- Machine-checkable schema (JSON Schema for the YAML) once a second recipe
  exists — premature to freeze types off a sample size of one.
- `verify` steps as executable references (paths into an acceptance suite)
  rather than prose, once the suite exists.
- Receipt record format is cst-8ih.3, not this document.
