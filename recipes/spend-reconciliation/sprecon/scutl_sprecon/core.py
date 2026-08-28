"""Reconciler: every manifest tool (sr_status / sr_reconcile /
sr_findings / sr_resolve) maps to one method here.

Design record (recipe sprc rev 1, cst-r0vz): the reconciler moves no
money and holds no credentials — no signing key, no kek, no provider
API key. It reads the agent's append-only books as FILES (spend.log,
earnings.log, refunds.log, instances.log — no import of the audited
code), discovers chain transfers via the read-only chain client, joins
a provider statement fetched by the recipe that owns the key, and
appends findings. Every record lands in exactly one category per pass
and the report states the conservation line (records in = records
categorized). Residues have exactly two buckets: a named honest
category with the explaining records, or an escalation carrying the
joined evidence verbatim. There is no fudge line; re-baselining is a
tokened op that records old anchor, new anchor, and why.

Its one write surface is its own state: findings.log (append-only,
same O_APPEND+fsync discipline as everything it audits), checkpoints,
and nothing else — a reconcile pass leaves every audited log
byte-identical, and the audited logs' reconciled prefixes are
digest-checkpointed so a book whose PAST changed escalates on sight.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# escalated findings need a human token to resolve; explained ones don't
ESCALATE = "escalated"
EXPLAINED = "explained"

FINDINGS_NAME = "findings.log"
CHECKPOINTS_NAME = "checkpoints.json"
FUNDING_NAME = "funding.log"

RESOLVE_OPS = ("resolve", "rebaseline")


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'sprecon-approve {op}' (out of band), then retry")
        self.op = op


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l]


def _prefix_digest(path: Path, lines: int) -> str:
    kept = path.read_text().splitlines()[:lines] if path.exists() else []
    return hashlib.sha256(("\n".join(kept)).encode()).hexdigest()


class Reconciler:
    def __init__(self, root: str | os.PathLike, chain,
                 wallet_dir: str | os.PathLike,
                 pserv_dir: str | os.PathLike | None = None,
                 prov_dir: str | os.PathLike | None = None,
                 clock_tolerance_secs: int = 900,
                 confirmation_deadline_hours: int = 24,
                 now: datetime | None = None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.chain = chain
        self.wallet_dir = Path(wallet_dir)
        self.pserv_dir = Path(pserv_dir) if pserv_dir else None
        self.prov_dir = Path(prov_dir) if prov_dir else None
        self.clock_tolerance = timedelta(seconds=clock_tolerance_secs)
        self.deadline = timedelta(hours=confirmation_deadline_hours)
        self._now = now

    # -- own state (the only write surface) -----------------------------

    @property
    def findings_log(self) -> Path:
        return self.root / FINDINGS_NAME

    @property
    def funding_log(self) -> Path:
        return self.root / FUNDING_NAME

    def _approvals(self) -> Path:
        d = self.root / "approvals"
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    def _consume_approval(self, op: str) -> None:
        token = self._approvals() / op
        if not token.exists():
            raise ApprovalRequired(op)
        token.unlink()

    def grant_approval(self, op: str) -> None:
        if op not in RESOLVE_OPS:
            raise ValueError(f"unknown op '{op}' (tokened: {RESOLVE_OPS})")
        (self._approvals() / op).write_text(_utcnow().isoformat())

    def now(self) -> datetime:
        return self._now or _utcnow()

    def _append_finding(self, record: dict) -> dict:
        record = {"ts": self.now().isoformat(),
                  "id": f"f{len(self.read_findings_raw()) + 1:04d}",
                  **record}
        line = json.dumps(record, separators=(",", ":"),
                          default=str) + "\n"
        fd = os.open(self.findings_log,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def read_findings_raw(self) -> list[dict]:
        return _read_jsonl(self.findings_log)

    def _checkpoints(self) -> dict:
        p = self.root / CHECKPOINTS_NAME
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_checkpoints(self, cp: dict) -> None:
        (self.root / CHECKPOINTS_NAME).write_text(json.dumps(cp, indent=1))

    # -- books ----------------------------------------------------------

    def _books(self) -> dict[str, Path]:
        books = {"spend": self.wallet_dir / "spend.log"}
        if self.pserv_dir:
            books["earnings"] = self.pserv_dir / "earnings.log"
            refunds = self.pserv_dir / "refunds.log"
            if refunds.exists():
                books["refunds"] = refunds
        if self.prov_dir:
            books["instances"] = self.prov_dir / "instances.log"
        return books

    def wallet_address(self) -> str:
        doc = json.loads((self.wallet_dir / "keystore.json").read_text())
        if "address_checksummed" in doc:      # public envelope field only
            return doc["address_checksummed"]
        return "0x" + doc["address"]

    # -- ops ------------------------------------------------------------

    def status(self) -> dict:
        cp = self._checkpoints()
        findings = self.findings()
        open_by_cat: dict[str, int] = {}
        for f in findings:
            if f["state"] == ESCALATE:
                open_by_cat[f["category"]] = \
                    open_by_cat.get(f["category"], 0) + 1
        baseline = next((f for f in self.read_findings_raw()
                         if f.get("category") == "baseline"), None)
        return {
            "baseline": baseline,
            "last_reconciled_block": cp.get("last_block"),
            "books": {name: {"present": path.exists(),
                             "checkpoint": cp.get("books", {}).get(name)}
                      for name, path in self._books().items()},
            "open_escalations": open_by_cat,
            "findings_total": len(findings),
        }

    def baseline(self, opening_balance: Decimal, block: int,
                 attestor: str) -> dict:
        """The one moment a human vouches for the past; approval-gated
        like re-baselining, because it IS the first baseline."""
        if any(f.get("category") == "baseline" and f.get("state") != "superseded"
               for f in self.read_findings_raw()):
            raise ValueError(
                "baseline exists; changing anchors is 'sprecon rebaseline'")
        self._consume_approval("rebaseline")
        return self._append_finding({
            "category": "baseline", "state": EXPLAINED,
            "evidence": {"opening_balance": str(opening_balance),
                         "block": block, "attestor": attestor},
            "note": "anchor: human-attested opening balance"})

    def rebaseline(self, opening_balance: Decimal, block: int,
                   reason: str) -> dict:
        self._consume_approval("rebaseline")
        old = next((f for f in reversed(self.read_findings_raw())
                    if f.get("category") == "baseline"), None)
        return self._append_finding({
            "category": "baseline", "state": EXPLAINED,
            "evidence": {"opening_balance": str(opening_balance),
                         "block": block,
                         "old_anchor": (old or {}).get("evidence"),
                         "reason": reason},
            "note": "re-baseline: old and new anchors recorded"})

    def findings(self, state: str | None = None) -> list[dict]:
        """Current state per finding id: state changes append, so the
        latest record per id wins; nothing is rewritten."""
        latest: dict[str, dict] = {}
        for rec in self.read_findings_raw():
            latest[rec.get("supersedes", rec["id"])] = rec
        out = [r for r in latest.values() if r.get("category") != "baseline"]
        if state:
            out = [r for r in out if r["state"] == state]
        return sorted(out, key=lambda r: r["id"])

    def resolve(self, finding_id: str, note: str) -> dict:
        target = next((f for f in self.findings()
                       if f.get("supersedes", f["id"]) == finding_id
                       or f["id"] == finding_id), None)
        if target is None:
            raise ValueError(f"unknown finding '{finding_id}'")
        if target["state"] == ESCALATE:
            self._consume_approval("resolve")
        return self._append_finding({
            "category": target["category"], "state": "resolved",
            "supersedes": finding_id,
            "evidence": target.get("evidence"), "note": note})

    # -- the pass -------------------------------------------------------

    def reconcile(self, billing: list[dict] | None = None) -> dict:
        now = self.now()
        cp = self._checkpoints()
        books = self._books()
        report_findings: list[dict] = []

        # a finding raised on a prior pass (same category, same evidence)
        # is reported again, not re-appended — findings.log stays one
        # record per event, and its state machine per id stays honest
        def _key(category: str, evidence) -> tuple:
            return (category,
                    json.dumps(evidence, sort_keys=True, default=str))
        seen = {_key(f["category"], f.get("evidence")): f
                for f in self.read_findings_raw()}

        def finding(category: str, state: str, evidence, note: str) -> None:
            key = _key(category, evidence)
            if key in seen:
                report_findings.append({**seen[key], "prior": True})
                return
            rec = self._append_finding({
                "category": category, "state": state,
                "evidence": evidence, "note": note})
            seen[key] = rec
            report_findings.append(rec)

        # 1. tamper check: an append-only book whose reconciled prefix
        # changed is evidence, not a parse error — check before reading.
        before_digests = {}
        for name, path in books.items():
            before_digests[name] = _prefix_digest(
                path, 10**9) if path.exists() else None
            old = cp.get("books", {}).get(name)
            if old and path.exists():
                got = _prefix_digest(path, old["lines"])
                if got != old["digest"]:
                    finding("log-prefix-tampered", ESCALATE,
                            {"book": name, "checkpoint_digest": old["digest"],
                             "current_digest": got,
                             "checkpoint_lines": old["lines"]},
                            "reconciled prefix changed under an append-only "
                            "book; the digests are the evidence")

        records_in = 0

        # 2. own books: spend.log — latest record per payment_id, settled
        # supersedes any reservation (re-auth re-signs the same nonce).
        spends = _read_jsonl(books["spend"])
        records_in += len(spends)
        latest: dict[str, dict] = {}
        for rec in spends:
            cur = latest.get(rec["payment_id"])
            if cur is None or cur.get("status") != "settled":
                latest[rec["payment_id"]] = rec

        # 3. chain discovery from the checkpointed block
        address = self.wallet_address()
        from_block = cp.get("last_block", 0) + 1 if "last_block" in cp else 0
        head = self.chain.head_block()
        transfers = self.chain.transfers(address, from_block, head)
        transfers += cp.get("carry_transfers", [])
        for t in transfers:
            t["amount"] = Decimal(str(t["amount"]))
        records_in += len(transfers)
        unmatched = {(t["tx"], t["direction"]): t for t in transfers}

        categorized = 0

        def match_transfer(tx: str, direction: str):
            return unmatched.pop((tx, direction), None)

        # 4. settled spends against chain
        for pid, rec in latest.items():
            age = now - datetime.fromisoformat(rec["ts"])
            if rec.get("status") == "settled":
                categorized += sum(
                    1 for r in spends if r["payment_id"] == pid)
                tx = rec.get("tx") or ""
                if not tx:
                    if Decimal(rec["amount"]) == 0:
                        continue  # v2 identity call: legitimate empty
                    finding("settle-without-evidence", ESCALATE, rec,
                            "nonzero settle recorded with no transaction")
                    continue
                if match_transfer(tx, "out") is not None:
                    continue  # book and chain agree
                status = self.chain.tx_status(tx)
                if status == "confirmed":
                    continue  # confirmed before this audit window opened
                if age <= self.deadline:
                    finding("pending-tx", EXPLAINED,
                            {"record": rec, "tx_status": status},
                            f"settle not yet visible on chain; escalates "
                            f"after {self.deadline}")
                elif status == "failed":
                    finding("settled-tx-failed", ESCALATE,
                            {"record": rec, "tx_status": status},
                            "book says settled, chain says failed, past "
                            "the confirmation deadline")
                else:
                    finding("pending-past-deadline", ESCALATE,
                            {"record": rec, "tx_status": status},
                            "settle unconfirmed past the deadline")
                continue

            # reservation never superseded by a settle
            categorized += sum(1 for r in spends if r["payment_id"] == pid)
            valid_before = datetime.fromtimestamp(
                rec["valid_before"], tz=timezone.utc)
            settled_late = next(
                (t for (tx, d), t in list(unmatched.items())
                 if d == "out" and t["amount"] == Decimal(rec["amount"])
                 and t["counterparty"].lower() == rec["to"].lower()), None)
            if settled_late is not None:
                match_transfer(settled_late["tx"], "out")
                finding("unrecorded-merchant-settle", ESCALATE,
                        {"reservation": rec, "transfer": settled_late},
                        "merchant settled a signed authorization the book "
                        "never recorded settled (2xx-no-tx path); joins on "
                        "amount+counterparty inside the reservation")
            elif valid_before > now:
                finding("live-reservation", EXPLAINED, rec,
                        "signed authorization the merchant may still settle")
            else:
                finding("expired-reservation", EXPLAINED, rec,
                        "authorization expired unsettled; cap headroom "
                        "released")

        # 5. earnings and refunds against chain
        earnings_in = Decimal("0")
        refunds_out = Decimal("0")
        for name, direction, sign in (("earnings", "in", 1),
                                      ("refunds", "out", -1)):
            if name not in books:
                continue
            recs = _read_jsonl(books[name])
            records_in += len(recs)
            categorized += len(recs)
            for rec in recs:
                amt = Decimal(rec["amount"])
                if sign > 0:
                    earnings_in += amt
                else:
                    refunds_out += amt
                tx = rec.get("tx") or ""
                if tx and match_transfer(tx, direction) is None \
                        and self.chain.tx_status(tx) != "confirmed":
                    finding(f"{name}-unconfirmed", ESCALATE, rec,
                            f"{name} record's transaction is not on chain")

        # 6. leftover transfers: discovery findings
        funding = _read_jsonl(self.funding_log)
        attested = {(f.get("tx") or "", Decimal(f["amount"]))
                    for f in funding}
        funded_in = Decimal("0")
        carry: list[dict] = []
        for t in list(unmatched.values()):
            if t["direction"] == "out":
                finding("unlogged-transfer-out", ESCALATE, t,
                        "USDC left the wallet and no book mentions it — "
                        "possible drain; never absorbed, never parked")
            elif (t["tx"], t["amount"]) in attested \
                    or ("", t["amount"]) in attested:
                funded_in += t["amount"]
                finding("attested-funding", EXPLAINED, t,
                        "matches a human-attested external funding event")
            else:
                finding("unattested-deposit", ESCALATE, t,
                        "USDC arrived with no attestation and no earnings "
                        "record; the invariant cannot close on trust")
            carry.append({**t, "amount": str(t["amount"])})
        categorized += len(transfers)

        # 7. billing leg — "absent" means expected-and-missing (a
        # statement-absent escalation); a pass with no provision books
        # at all has no billing leg to check and says so honestly.
        billing_leg = "absent" if self.prov_dir is not None else "not-in-scope"
        if billing is not None and self.prov_dir is not None:
            billing_leg = "present"
            events = _read_jsonl(books["instances"])
            records_in += len(events) + len(billing)
            categorized += len(events) + len(billing)
            created = {e["id"]: e for e in events
                       if e.get("event") == "created"}
            destroyed = {e["id"]: e for e in events
                         if e.get("event") == "destroyed"}
            for line in billing:
                iid = line.get("instance_id") or line.get("id")
                if iid not in created:
                    finding("foreign-invoice-line", ESCALATE, line,
                            "invoice line for an instance no book created — "
                            "foreign spend on our rail")
                    continue
                if iid in destroyed and line.get("period_start") and \
                        datetime.fromisoformat(line["period_start"]) > \
                        datetime.fromisoformat(destroyed[iid]["ts"]) + \
                        self.clock_tolerance:
                    finding("billing-after-destroy", ESCALATE,
                            {"line": line, "destroyed": destroyed[iid]},
                            "charges continuing past a logged destroy")
                    continue
                est = Decimal(created[iid].get("hourly_usd", "0"))
                if "hourly_usd" not in line and "hours" not in line:
                    # invoice granularity coarser than events: the join
                    # loosens to period totals, and SAYS so
                    finding("aggregated-lines", EXPLAINED,
                            {"line": line, "estimated_hourly": str(est)},
                            "aggregated invoice line: no per-hour figure "
                            "to compare; join loosened to period totals")
                    continue
                billed = (Decimal(str(line["amount"]))
                          / Decimal(str(line["hours"]))
                          if "hours" in line
                          else Decimal(str(line["hourly_usd"])))
                if billed > est:
                    finding("billing-over-estimate", ESCALATE,
                            {"line": line, "estimated_hourly": str(est)},
                            "billed above our round-up estimate — the "
                            "designed conservatism only covers one side")
                elif billed < est:
                    finding("estimate-vs-actual", EXPLAINED,
                            {"line": line, "estimated_hourly": str(est)},
                            "own 730h/mo round-up >= billed actual; "
                            "designed conservatism")
        elif self.prov_dir is not None:
            finding("statement-absent", ESCALATE,
                    {"leg": "billing", "book": str(books["instances"])},
                    "no provider statement for this pass; absent is never "
                    "clean — instance findings may change when one arrives")

        # 8. balance invariant — independent cross-check on the same pass
        baseline = next((f for f in reversed(self.read_findings_raw())
                         if f.get("category") == "baseline"), None)
        invariant = {"checked": False}
        if baseline:
            settled_out = sum(
                (Decimal(r["amount"]) for r in spends
                 if r.get("status") == "settled"), Decimal("0"))
            expected = (Decimal(baseline["evidence"]["opening_balance"])
                        - settled_out + earnings_in - refunds_out
                        + funded_in)
            actual = self.chain.usdc_balance(address)
            residue = actual - expected
            # findings already raised on this pass EXPLAIN the arithmetic
            # with a sign per category — money that left off-book pulls
            # the balance below expectation, money the books subtracted
            # but the chain never moved pushes it above — so the residue
            # finding fires only on the truly unaccounted remainder
            unrecorded = sum(
                (Decimal(f["evidence"]["reservation"]["amount"])
                 for f in report_findings
                 if f["category"] == "unrecorded-merchant-settle"),
                Decimal("0"))
            pending_back = sum(
                (Decimal(f["evidence"]["record"]["amount"])
                 for f in report_findings
                 if f["category"] in ("pending-tx", "pending-past-deadline",
                                      "settled-tx-failed")),
                Decimal("0"))
            drains = sum(
                (Decimal(str(f["evidence"]["amount"])) for f in
                 report_findings
                 if f["category"] == "unlogged-transfer-out"), Decimal("0"))
            deposits = sum(
                (Decimal(str(f["evidence"]["amount"])) for f in
                 report_findings
                 if f["category"] == "unattested-deposit"), Decimal("0"))
            explained = deposits - drains - unrecorded + pending_back
            invariant = {"checked": True, "expected": str(expected),
                         "actual": str(actual), "residue": str(residue),
                         "explained_by_findings": str(explained)}
            if residue != explained:
                finding("balance-residue", ESCALATE, invariant,
                        "invariant does not close and the discovered "
                        "findings do not explain the residue")

        # 9. checkpoints: books' reconciled prefixes + last block; carry
        # transfers that remain unmatched so later passes can re-join them
        cp["books"] = {
            name: {"lines": len(path.read_text().splitlines())
                   if path.exists() else 0,
                   "digest": before_digests[name] or _prefix_digest(path, 0)}
            for name, path in books.items()}
        cp["last_block"] = head
        cp["carry_transfers"] = carry
        self._save_checkpoints(cp)

        # 10. read-only proof + conservation line
        for name, path in books.items():
            if path.exists():
                after = _prefix_digest(path, 10**9)
                if after != before_digests[name]:
                    finding("self-audit-write", ESCALATE, {"book": name},
                            "an audited log changed during the pass")

        escalations = [f for f in report_findings if f["state"] == ESCALATE]
        return {
            "records_in": records_in,
            "records_categorized": categorized,
            "conservation_ok": records_in == categorized,
            "legs": {"books": "present", "chain": "present",
                     "billing": billing_leg},
            "invariant": invariant,
            "findings": report_findings,
            "open_escalations": len(escalations),
        }
