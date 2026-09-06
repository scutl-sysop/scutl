"""Recipe IR loader: the slice of the manifest SMUTbench derives from.

Schema invariant 3 (recipe-schema.md): `contracts` MUST be mock-derivable
with no reference to `bindings`. This module is where that invariant is
cashed — it reads contracts, component invariants, and execute boundary
behaviors, and never looks at the bindings block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Contract:
    role: str                       # facilitator | faucet | chain | ...
    ops: list[str]
    failure_modes: list[str]


@dataclass
class RecipeIR:
    recipe_id: str
    rev: int
    contracts: dict[str, Contract]
    invariants: list[str]           # union of component invariants
    guardrails: list[str]
    parameters: dict[str, dict]     # cap_per_tx / cap_daily defaults etc.
    manifest_path: Path = field(repr=False, default=None)

    def failure_modes(self) -> list[tuple[str, str]]:
        """(role, mode) pairs — the scenario menu, straight from the IR."""
        return [(c.role, m) for c in self.contracts.values()
                for m in c.failure_modes]


def load(manifest_path: str | Path) -> RecipeIR:
    manifest_path = Path(manifest_path).expanduser()
    doc = yaml.safe_load(manifest_path.read_text())

    contracts = {}
    for role, body in (doc.get("contracts") or {}).items():
        ops = body.get("ops") or []
        modes = body.get("failure_modes") or []
        if not ops:
            raise ValueError(
                f"contract '{role}' has no ops — not mock-derivable "
                f"(recipe-schema invariant 3)")
        contracts[role] = Contract(role=role, ops=list(ops),
                                   failure_modes=list(modes))
    if not contracts:
        raise ValueError("manifest has no contracts block; nothing to mock")

    invariants = []
    for comp in (doc.get("components") or {}).values():
        invariants.extend(comp.get("invariants") or [])

    execute = doc.get("execute") or {}
    return RecipeIR(
        recipe_id=doc["recipe"]["id"],
        rev=int(doc["recipe"]["rev"]),
        contracts=contracts,
        invariants=invariants,
        guardrails=list(execute.get("guardrails") or []),
        parameters=doc.get("parameters") or {},
        manifest_path=manifest_path,
    )
