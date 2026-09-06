"""Provider-side mock for recipe #3 (provision), from its contracts:

  provider: plans / create_instance / list_instances / get_instance /
      destroy_instance / dns record CRUD inside one domain.
      Failure modes: transient-timeout, rate-limited,
      create-quota-exceeded, instance-stuck-pending,
      destroy-races-billing.

Implements the same surface as scutl_prov.network.VultrClient so the
real Manager runs unmodified against it. Same design rule as the other
benches: surface details (ids, IPs) randomize per seed; the behavioral
contract holds.

Lifecycle model: a created instance is "pending" with no IP until
`activation_polls` further list/get calls have observed it, then
"active" with an IP — unless the stuck-pending fault is armed.
"""

from __future__ import annotations

import random

from scutl_prov.network import PermanentError, TransientError


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


PLANS = [
    {"id": "vc2-1c-1gb", "hourly_cost": "0.0069"},
    {"id": "vc2-2c-4gb", "hourly_cost": "0.0274"},
    {"id": "vc2-4c-8gb", "hourly_cost": "0.0548"},   # over the twin ceiling
    {"id": "vbm-24c-256gb", "hourly_cost": "1.6438"},  # bare metal, way over
]

REGIONS = ["ewr", "ams", "fra"]


class MockProvider:
    """contracts.provider — VultrClient surface over an in-memory account."""

    def __init__(self, rng: random.Random, activation_polls: int = 2):
        self.rng = rng
        self.activation_polls = activation_polls
        self.fault: str | None = None       # armed failure mode
        self.fault_times = 1
        self.instances: dict[str, dict] = {}
        self._observed: dict[str, int] = {}  # id -> list/get sightings
        self.create_calls = 0
        self.destroy_calls: list[str] = []
        self.records: list[dict] = []        # dns records, one domain
        self._rec_seq = 0

    # -- fault plumbing --------------------------------------------------
    def _pop_fault(self, mode: str) -> bool:
        if self.fault == mode and self.fault_times > 0:
            self.fault_times -= 1
            if self.fault_times == 0:
                self.fault = None
            return True
        return False

    # -- plans -----------------------------------------------------------
    def plans(self) -> list[dict]:
        return [dict(p) for p in PLANS]

    # -- instances -------------------------------------------------------
    def _new_instance(self, plan: str, region: str, label: str) -> dict:
        iid = _hex(self.rng, 8) + "-" + _hex(self.rng, 4)
        inst = {"id": iid, "label": label, "plan": plan, "region": region,
                "status": "pending", "power_status": "running",
                "main_ip": "0.0.0.0",
                "date_created": "2026-08-20T00:00:00+00:00"}
        self.instances[iid] = inst
        self._observed[iid] = 0
        return inst

    def create_instance(self, plan: str, region: str, label: str,
                        os_id: int = 2136,
                        user_data: str | None = None) -> dict:
        self.create_calls += 1
        if self._pop_fault("rate-limited"):
            raise TransientError("vultr 429: rate limited")
        if self._pop_fault("create-quota-exceeded"):
            raise PermanentError(
                "vultr 400: instance quota exceeded for this account "
                "(provider-side limit; raising it is a human/portal action)")
        if self._pop_fault("transient-timeout"):
            # the request LANDED; only the ack was lost. The instance
            # exists at the provider but was never logged — it will show
            # as foreign in reconciliation, exactly the ambiguity the
            # list-before-retry guardrail exists for.
            self._new_instance(plan, region, label)
            raise TransientError("vultr unreachable: timed out")
        return dict(self._new_instance(plan, region, label))

    def _tick(self, iid: str) -> None:
        inst = self.instances[iid]
        self._observed[iid] += 1
        if (inst["status"] == "pending"
                and inst.get("stuck") is not True
                and self._observed[iid] >= self.activation_polls):
            inst["status"] = "active"
            inst["main_ip"] = ("198.51.100."
                               + str(self.rng.randrange(1, 254)))

    def list_instances(self) -> list[dict]:
        for iid in list(self.instances):
            self._tick(iid)
        return [dict(i) for i in self.instances.values()]

    def get_instance(self, instance_id: str) -> dict:
        if instance_id not in self.instances:
            raise PermanentError(f"vultr 404: instance {instance_id}")
        self._tick(instance_id)
        return dict(self.instances[instance_id])

    def destroy_instance(self, instance_id: str) -> None:
        self.destroy_calls.append(instance_id)
        if self._pop_fault("destroy-races-billing"):
            # destroy took effect; the ack raced billing and was lost.
            # A retry must be answered idempotently (accepted), never
            # not-found — that asymmetry is what makes retry safe.
            self.instances.pop(instance_id, None)
            raise TransientError("vultr unreachable: timed out")
        self.instances.pop(instance_id, None)   # idempotent accept

    # -- scenario seeding ------------------------------------------------
    def seed_foreign(self, label: str = "someone-elses-box") -> dict:
        """An instance the provider shows but instances.log does not:
        foreign, reported, never touched."""
        inst = self._new_instance(
            self.rng.choice([p["id"] for p in PLANS[:2]]),
            self.rng.choice(REGIONS), label)
        inst["status"] = "active"
        inst["main_ip"] = "203.0.113." + str(self.rng.randrange(1, 254))
        return dict(inst)

    # -- DNS (one domain: the delegated subzone) -------------------------
    def list_records(self, domain: str) -> list[dict]:
        return [dict(r) for r in self.records]

    def create_record(self, domain: str, name: str, rtype: str, value: str,
                      ttl: int = 300) -> dict:
        self._rec_seq += 1
        rec = {"id": f"rec-{self._rec_seq}", "name": name,
               "type": rtype, "data": value, "ttl": ttl}
        self.records.append(rec)
        return dict(rec)

    def delete_record(self, domain: str, record_id: str) -> None:
        before = len(self.records)
        self.records = [r for r in self.records if r["id"] != record_id]
        if len(self.records) == before:
            raise PermanentError(f"vultr 404: record {record_id}")
