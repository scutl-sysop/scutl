"""Provider-side mocks for recipe #13 (gpu-rental), from its contracts:

  pods:  create / get / delete / list.
         Failure modes: create-succeeds-status-lies,
         delete-accepted-pod-persists, price-differs-from-catalog,
         foreign-pod-appears, stock-exhausted, api-timeout-mid-create.
  stock: availability. Failure modes: stale-availability,
         region-mismatch.

Implements the same surfaces as scutl_gpod.network.PodsClient /
StockClient so the real Manager runs unmodified against them. Surface
details (pod ids, IPs, ports) randomize per seed; the behavioral
contract holds.

Lifecycle model mirrors the live rail (first-cycle observation,
2026-09-03): a created pod is RUNNING immediately but publicIp is
empty and portMappings null until `portmap_polls` further list/get
sightings — the ~30-40s mapping lag every run night waits through.
"""

from __future__ import annotations

import random

from scutl_gpod.network import Absent, PermanentError, TransientError


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdefghijklmnopqrstuvwxyz")
                   for _ in range(n))


CATALOG = {
    "NVIDIA GeForce RTX 4090": "0.74",
    "NVIDIA RTX PRO 4000 Blackwell": "0.57",
    "NVIDIA H100 80GB HBM3": "2.79",   # over every twin ceiling
}

REGION = "EU-RO-1"


class MockPods:
    """contracts.pods — PodsClient surface over an in-memory account."""

    def __init__(self, rng: random.Random, portmap_polls: int = 2):
        self.rng = rng
        self.portmap_polls = portmap_polls
        self.fault: str | None = None
        self.fault_times = 1
        self.pods: dict[str, dict] = {}
        self._observed: dict[str, int] = {}
        self.create_calls = 0
        self.delete_calls: list[str] = []
        self.price_lie: str | None = None   # costPerHr differing from catalog
        self.status_lie = False             # pod never actually runs
        self.undead = False                 # delete accepted, pod persists
        self.base = "https://twin.example.invalid/v1"

    def _pop_fault(self, mode: str) -> bool:
        if self.fault == mode and self.fault_times > 0:
            self.fault_times -= 1
            if self.fault_times == 0:
                self.fault = None
            return True
        return False

    def _new_pod(self, spec: dict) -> dict:
        pid = _hex(self.rng, 14)
        pod = {"id": pid, "name": spec.get("name", "pod"),
               "desiredStatus": "EXITED" if self.status_lie else "RUNNING",
               "costPerHr": float(self.price_lie
                                  or CATALOG[spec["gpuTypeIds"][0]]),
               "machineId": _hex(self.rng, 12),
               "publicIp": "", "portMappings": None,
               "gpuCount": 1, "imageName": spec.get("imageName"),
               "lastStartedAt": "2026-09-03 21:00:00 +0000 UTC",
               "spec": dict(spec)}
        self.pods[pid] = pod
        self._observed[pid] = 0
        return pod

    def create_pod(self, spec: dict) -> dict:
        self.create_calls += 1
        if self._pop_fault("stock-exhausted"):
            raise PermanentError(
                "runpod 500: There are no longer any instances available "
                "with the requested specifications. Please refresh and "
                "try again.")
        if self._pop_fault("api-timeout-mid-create"):
            # the request LANDED; only the ack was lost. The pod exists
            # and bills but was never logged — it will show as foreign
            # in reconciliation, exactly the ambiguity the
            # list-before-retry guardrail exists for.
            self._new_pod(spec)
            raise TransientError("runpod unreachable: timed out")
        return {k: v for k, v in self._new_pod(spec).items() if k != "spec"}

    def _tick(self, pid: str) -> None:
        pod = self.pods[pid]
        self._observed[pid] += 1
        if (pod["desiredStatus"] == "RUNNING" and not pod["publicIp"]
                and self._observed[pid] >= self.portmap_polls):
            pod["publicIp"] = "213.173.98." + str(self.rng.randrange(1, 254))
            pod["portMappings"] = {"22": self.rng.randrange(30000, 40000)}

    def get_pod(self, pod_id: str) -> dict:
        if pod_id not in self.pods:
            raise Absent()
        self._tick(pod_id)
        return {k: v for k, v in self.pods[pod_id].items() if k != "spec"}

    def delete_pod(self, pod_id: str) -> None:
        self.delete_calls.append(pod_id)
        if self.undead:
            return           # accepted; the pod persists and bills
        self.pods.pop(pod_id, None)   # idempotent accept

    def list_pods(self) -> list[dict]:
        for pid in list(self.pods):
            self._tick(pid)
        return [{k: v for k, v in p.items() if k != "spec"}
                for p in self.pods.values()]

    # -- scenario seeding ------------------------------------------------
    def seed_foreign(self, name: str = "console-made") -> dict:
        pod = self._new_pod({"name": name,
                             "gpuTypeIds": ["NVIDIA GeForce RTX 4090"],
                             "imageName": "runpod/pytorch:x-devel"})
        pod["publicIp"] = "213.173.98." + str(self.rng.randrange(1, 254))
        pod["portMappings"] = {"22": self.rng.randrange(30000, 40000)}
        return dict(pod)


class MockStock:
    """contracts.stock — StockClient surface."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.stock: dict[str, dict[str, str | None]] = {
            g: {REGION: "High"} for g in CATALOG}
        self.prices = dict(CATALOG)

    def catalog_price(self, gpu_type: str):
        from decimal import Decimal
        p = self.prices.get(gpu_type)
        return Decimal(p) if p is not None else None

    def availability(self, gpu_type: str, region: str | None = None) -> dict:
        centers = {dc: st for dc, st in
                   self.stock.get(gpu_type, {}).items()
                   if region is None or dc == region}
        return {"gpu_type": gpu_type, "region": region, "stock": centers,
                "available": any(v for v in centers.values())}
