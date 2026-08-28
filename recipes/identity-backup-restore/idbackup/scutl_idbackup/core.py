"""Archivist: every manifest tool (ib_status / ib_manifest / ib_verify /
ib_rehearse) maps to one method here.

Design record (recipe idbr rev 1, cst-jfou): the agent never moves,
prints, or transmits key material. What the agent authors is the backup
MANIFEST — the artifact list with a sha256 per file (the kek included,
closing the unverifiable-kek gap), the owner address, log checkpoints,
and the restore procedure. The human performs the copy; verification
then compares hashes and needs no secret and no approval. The rehearsal
is the one op that decrypts, so it is the one op that is gated
(approval token + panic + tombstone), and it restores into a FRESH
temporary directory — the live state dir is opened read-only throughout
and its digests are re-checked afterwards to prove it.

Layering: works over any scutl_signer StateDir root, msigner custody
files included when present. Counters reconcile arithmetically: the
manifest checkpoints spend.log (line count + settled total); a restored
log that comes up short is reported as an exact delta, never smoothed
over — going live over a delta is a human call that must repeat the
numbers (amnesia-policy: reconcile-or-approve).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scutl_signer.state import StateDir

# identity artifacts by exact name, when present at the state root
KNOWN_FILES = (
    "keystore.json", "kek", "network.json", "caps.json", "backup.marker",
    "tombstone.json", "custody.json", "ceremony.json", "ratchet.json",
    "clock.json", "panic.json", "sweep.json", "owned-resources.json",
)
# never part of the identity: consumables, locks, and this component's own
# records (the manifest describes the identity, it is not part of it)
EXCLUDED = ("approvals", "cap.lock", "backup-manifest.json",
            "attestations.json", "rehearsal.json")

MANIFEST_NAME = "backup-manifest.json"

RESTORE_PROCEDURE = [
    "1. Create a fresh empty directory, mode 0700; never restore over a "
    "live state dir.",
    "2. Copy every manifested artifact into it; keystore.json and kek at "
    "mode 0600.",
    "3. Run 'idbackup verify --backup-dir <dir>' against this manifest — "
    "all digests must match before anything decrypts.",
    "4. Run 'idbackup rehearse --backup-dir <dir>' (human-approved): the "
    "restored key must derive the manifested owner address and produce "
    "one real signature.",
    "5. Compare the reported counter delta to zero; going live over a "
    "non-zero delta requires an explicit human approval that names it.",
    "6. Point SCUTL_STATE (or the msigner root) at the restored directory.",
]


class Tombstoned(Exception):
    """Identity is revoked; the tombstone is the report."""


class Panicked(Exception):
    """Panic marker present at the state root; rehearsal refuses."""


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'idbackup-approve {op}' (out of band), then retry")
        self.op = op


class UnverifiedBackup(Exception):
    """Rehearsal refuses a copy that does not verify clean (or stale)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _log_lines(path: Path) -> int:
    return len([l for l in path.read_text().splitlines() if l])


def _settled_total(path: Path) -> Decimal:
    total = Decimal("0")
    if path.exists():
        for line in path.read_text().splitlines():
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") == "settled":
                total += Decimal(rec["amount"])
    return total


def _owner_address(keystore_path: Path) -> str:
    doc = json.loads(keystore_path.read_text())   # public envelope fields
    if "address_checksummed" in doc:
        return doc["address_checksummed"]
    return "0x" + doc["address"]


class Archivist:
    def __init__(self, state_root: str | os.PathLike | None = None,
                 clock=None):
        root = Path(
            state_root
            or os.environ.get("SCUTL_IDBACKUP_STATE")
            or os.environ.get("SCUTL_STATE")
            or Path.home() / ".scutl" / "wallet"
        ).expanduser()
        self.state = StateDir(root)
        self.root = self.state.root
        self._clock = clock or _utcnow

    # -- paths -------------------------------------------------------------
    @property
    def manifest_file(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def attestations_file(self) -> Path:
        return self.root / "attestations.json"

    @property
    def rehearsal_file(self) -> Path:
        return self.root / "rehearsal.json"

    # -- shared helpers ----------------------------------------------------
    def _identity_files(self) -> list[str]:
        names = [n for n in KNOWN_FILES if (self.root / n).exists()]
        for p in sorted(self.root.iterdir()):
            if (p.is_file() and p.name.endswith(".log")
                    and p.name not in names):
                names.append(p.name)
        return names

    def _load_manifest(self) -> dict:
        if not self.manifest_file.exists():
            raise FileNotFoundError(
                f"{self.manifest_file} — run 'idbackup manifest' first")
        return json.loads(self.manifest_file.read_text())

    def _check_not_tombstoned(self) -> None:
        if self.state.tombstone.exists():
            raise Tombstoned(
                json.loads(self.state.tombstone.read_text())["address"])

    def _check_not_panicked(self) -> None:
        panic = self.root / "panic.json"
        if panic.exists():
            rec = json.loads(panic.read_text())
            raise Panicked(
                f"panicked at {rec.get('panicked_at')} "
                f"({rec.get('reason')}); rehearsal waits for unpanic")

    def _consume_approval(self, op: str) -> None:
        token = self.state.approvals / f"{op}.token"
        if not token.exists():
            raise ApprovalRequired(op)
        token.unlink()

    def _live_digests(self) -> dict[str, str]:
        return {n: _sha256(self.root / n) for n in self._identity_files()}

    # -- ib_manifest -------------------------------------------------------
    def manifest(self, backup_locations: int = 2,
                 staleness_horizon_days: int = 30) -> dict:
        """Write/refresh backup-manifest.json from the LIVE state dir.
        Reads everything, moves nothing; the only write is the manifest
        itself. Digests stand in for secrets — the kek's sha256 makes the
        kek backup verifiable without its bytes ever leaving this box."""
        if not self.state.keystore.exists():
            raise FileNotFoundError(
                f"{self.state.keystore} — no identity to manifest")
        names = self._identity_files()
        artifacts = {}
        logs = {}
        for n in names:
            p = self.root / n
            artifacts[n] = {"sha256": _sha256(p), "bytes": p.stat().st_size}
            if n.endswith(".log"):
                logs[n] = {"lines": _log_lines(p)}
        doc = {
            "manifest_version": 1,
            "created_at": self._clock().isoformat(),
            "owner_address": _owner_address(self.state.keystore),
            "network": self.state.load_network(),
            "backup_locations": backup_locations,
            "staleness_horizon_days": staleness_horizon_days,
            "artifacts": artifacts,
            "logs": logs,
            "counters": {
                "spend_settled_total": str(_settled_total(self.state.spend_log)),
                "spend_lines": logs.get("spend.log", {}).get("lines", 0),
            },
            "restore_procedure": RESTORE_PROCEDURE,
            "human_copy_note": (
                "HUMAN: copy every artifact listed above to "
                f"{backup_locations} independent offline locations and "
                "attest each with 'idbackup-attest --location <label>'. "
                "The agent never performs this copy."),
        }
        # a fresh manifest invalidates old attestations: the copy the
        # human attested no longer matches what must be backed up
        if self.attestations_file.exists():
            self.attestations_file.unlink()
        self.manifest_file.write_text(json.dumps(doc, indent=2))
        return {"manifest": str(self.manifest_file),
                "owner_address": doc["owner_address"],
                "artifacts": len(artifacts),
                "kek_digest_recorded": "kek" in artifacts,
                "counters": doc["counters"],
                "attestations_reset": True,
                "next": doc["human_copy_note"]}

    # -- ib_verify ---------------------------------------------------------
    def verify(self, backup_dir: str | os.PathLike) -> dict:
        """Check a BACKUP COPY against the manifest. Secret-free: hashes
        and compares, decrypts nothing, needs no approval. Verdicts:
        ok | stale | partial | corrupt | foreign — plus tombstoned as a
        health override (a revoked identity never verifies healthy)."""
        man = self._load_manifest()
        backup = Path(backup_dir).expanduser()
        if not backup.is_dir():
            raise FileNotFoundError(f"backup dir missing: {backup}")

        missing, mismatched, truncated = [], [], []
        for name, meta in man["artifacts"].items():
            p = backup / name
            if not p.exists():
                missing.append(name)
                continue
            if _sha256(p) == meta["sha256"]:
                continue
            if name.endswith(".log"):
                have = _log_lines(p)
                want = man["logs"].get(name, {}).get("lines", 0)
                if have < want:
                    truncated.append({"log": name, "lines_expected": want,
                                      "lines_present": have})
                    continue
            mismatched.append(name)

        extra = sorted(
            p.name for p in backup.iterdir()
            if p.is_file() and p.name not in man["artifacts"]
            and p.name not in EXCLUDED)

        # foreign: the copy is internally plausible but belongs to a
        # different wallet — the address says so without any decryption
        foreign = None
        b_keystore = backup / "keystore.json"
        if b_keystore.exists():
            try:
                addr = _owner_address(b_keystore)
                if addr.lower() != man["owner_address"].lower():
                    foreign = {"backup_address": addr,
                               "manifest_owner": man["owner_address"]}
            except (ValueError, KeyError, json.JSONDecodeError):
                pass    # unparseable keystore is already 'mismatched'

        # counter delta for a truncated spend log: the exact re-armed
        # amount, named with numbers
        counter_delta = None
        if any(t["log"] == "spend.log" for t in truncated):
            restored_total = _settled_total(backup / "spend.log")
            want_total = Decimal(man["counters"]["spend_settled_total"])
            counter_delta = {
                "spend_settled_expected": str(want_total),
                "spend_settled_present": str(restored_total),
                "delta": str(want_total - restored_total),
            }

        # stale: the copy matches its manifest, but the manifest no longer
        # matches the LIVE identity (drift), or it has aged past horizon
        drifted, aged = [], False
        clean = not (missing or mismatched or truncated or extra or foreign)
        if clean:
            live = self._live_digests()
            for n, dig in live.items():
                if man["artifacts"].get(n, {}).get("sha256") != dig:
                    drifted.append(n)
            age_days = (self._clock()
                        - datetime.fromisoformat(man["created_at"])).days
            aged = age_days > man.get("staleness_horizon_days", 30)

        if foreign:
            verdict = "foreign"
        elif missing:
            verdict = "partial"
        elif mismatched or truncated or extra:
            verdict = "corrupt"
        elif drifted or aged:
            verdict = "stale"
        else:
            verdict = "ok"

        tombstoned = self.state.tombstone.exists()
        return {
            "verdict": verdict,
            "healthy": verdict == "ok" and not tombstoned,
            "tombstoned": tombstoned,
            "backup_dir": str(backup),
            "owner_address": man["owner_address"],
            "checked": len(man["artifacts"]),
            "missing": missing,
            "mismatched": mismatched,
            "truncated": truncated,
            "extra": extra,
            "foreign": foreign,
            "counter_delta": counter_delta,
            "drifted_since_manifest": drifted,
            "manifest_aged_out": aged,
        }

    # -- ib_rehearse -------------------------------------------------------
    def rehearse(self, backup_dir: str | os.PathLike,
                 probe=None) -> dict:
        """The TRUE restore: the verified copy is restored into a fresh
        temporary directory, the keystore decrypts under the backed-up
        kek, the derived address must equal the manifested owner, the
        restored key produces one real signature, and the restored logs
        reconcile against the checkpointed counters. The live state dir
        is read-only throughout — proven by digest comparison after.
        Approval-, panic-, and tombstone-gated. `probe`, when given, is
        called as probe(address, resource_record) for each owned resource
        and returns True if the live signed probe confirms ownership."""
        self._check_not_panicked()
        self._check_not_tombstoned()
        self._consume_approval("rehearse")

        man = self._load_manifest()
        before = self._live_digests()

        report = self.verify(backup_dir)
        # amnesia-policy (reconcile-or-approve): a copy whose ONLY defect
        # is log truncation still rehearses — the rehearsal is where the
        # counter delta gets named with numbers. Everything else refuses.
        only_truncation = (report["verdict"] == "corrupt"
                           and report["truncated"]
                           and not (report["missing"] or report["mismatched"]
                                    or report["extra"] or report["foreign"]))
        if report["verdict"] not in ("ok", "stale") and not only_truncation:
            raise UnverifiedBackup(
                f"backup verifies '{report['verdict']}' "
                f"(missing={report['missing']} mismatched={report['mismatched']} "
                f"truncated={[t['log'] for t in report['truncated']]} "
                f"extra={report['extra']}); fix the copy before rehearsing")

        backup = Path(backup_dir).expanduser()
        fresh = Path(tempfile.mkdtemp(prefix="idbr-rehearsal-"))
        os.chmod(fresh, 0o700)
        try:
            for name in man["artifacts"]:
                src = backup / name
                dst = fresh / name
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o600)

            from eth_account import Account
            from eth_account.messages import encode_defunct

            restored = Account.from_key(Account.decrypt(
                json.loads((fresh / "keystore.json").read_text()),
                (fresh / "kek").read_text().strip()))
            owner = man["owner_address"]
            if restored.address.lower() != owner.lower():
                raise ValueError(
                    f"restored key derives {restored.address}, manifest "
                    f"owner is {owner} — this backup does NOT restore "
                    f"this identity")

            probe_text = (f"idbr-rehearsal {self._clock().isoformat()} "
                          f"{owner}")
            msg = encode_defunct(text=probe_text)
            signed = restored.sign_message(msg)
            recovered = Account.recover_message(
                msg, signature=signed.signature)
            signature_proven = recovered.lower() == owner.lower()

            restored_total = _settled_total(fresh / "spend.log")
            want_total = Decimal(man["counters"]["spend_settled_total"])
            delta = want_total - restored_total
            counter_delta = {
                "spend_settled_expected": str(want_total),
                "spend_settled_restored": str(restored_total),
                "delta": str(delta),
            }

            owned = []
            reg = fresh / "owned-resources.json"
            if reg.exists():
                for rec in json.loads(reg.read_text()):
                    entry = {
                        "resource": rec.get("resource"),
                        "provider": rec.get("provider"),
                        "address_matches":
                            rec.get("owning_address", "").lower()
                            == owner.lower(),
                    }
                    if probe is not None:
                        entry["probed_owned"] = bool(probe(owner, rec))
                    owned.append(entry)
        finally:
            shutil.rmtree(fresh, ignore_errors=True)

        live_untouched = self._live_digests() == before
        passed = (signature_proven and delta == 0 and live_untouched
                  and all(r["address_matches"] for r in owned)
                  and all(r.get("probed_owned", True) for r in owned))
        rec = {
            "rehearsed_at": self._clock().isoformat(),
            "address": owner,
            "rehearsal_passed": passed,
            "signature_proven": signature_proven,
            "counter_delta": counter_delta,
            "owned_resources": owned,
            "live_untouched": live_untouched,
        }
        self.rehearsal_file.write_text(json.dumps(rec, indent=2))
        if delta != 0:
            rec["amnesia_warning"] = (
                f"restored counters are behind by {delta} USDC settled "
                f"spend — going live on this backup re-arms that budget. "
                f"A live restore over this delta requires an explicit "
                f"human approval that repeats these numbers.")
        return rec

    # -- ib_status ---------------------------------------------------------
    def status(self) -> dict:
        """Backup health at a glance. Never gated; read-only."""
        out: dict = {
            "configured": self.state.keystore.exists(),
            "tombstoned": self.state.tombstone.exists(),
            "panicked": (self.root / "panic.json").exists(),
        }
        if not self.manifest_file.exists():
            return {**out, "manifest": None,
                    "next": "run 'idbackup manifest' to author the backup "
                            "manifest"}
        man = self._load_manifest()
        now = self._clock()
        age_days = (now - datetime.fromisoformat(man["created_at"])).days
        drifted = [n for n, dig in self._live_digests().items()
                   if man["artifacts"].get(n, {}).get("sha256") != dig]
        attestations = []
        if self.attestations_file.exists():
            attestations = json.loads(self.attestations_file.read_text())
        rehearsal = None
        if self.rehearsal_file.exists():
            rehearsal = json.loads(self.rehearsal_file.read_text())
        owned_count = 0
        reg = self.root / "owned-resources.json"
        if reg.exists():
            owned_count = len(json.loads(reg.read_text()))
        return {
            **out,
            "manifest": {"created_at": man["created_at"],
                         "age_days": age_days,
                         "artifacts": len(man["artifacts"]),
                         "owner_address": man["owner_address"]},
            "stale": bool(drifted) or age_days > man.get(
                "staleness_horizon_days", 30),
            "drifted_since_manifest": drifted,
            "attestations": {"required": man.get("backup_locations", 2),
                             "recorded": len(attestations),
                             "locations": [a["location"] for a in attestations]},
            "last_rehearsal": rehearsal,
            "owned_resources": owned_count,
        }

    # -- registry hook (the buying recipe's write path) --------------------
    def record_owned_resource(self, provider: str, resource: str,
                              price: str | None = None,
                              evidence: str | None = None) -> dict:
        """Append one owned-resource record. Called by the BUYING recipe
        at purchase time (x402v2 hook) — the durable answer to 'what does
        this key own', verified at restore by address equality."""
        self._check_not_tombstoned()
        owner = _owner_address(self.state.keystore)
        reg = self.root / "owned-resources.json"
        records = json.loads(reg.read_text()) if reg.exists() else []
        rec = {"provider": provider, "resource": resource,
               "owning_address": owner,
               "acquired_at": self._clock().isoformat(),
               "price": price, "evidence": evidence}
        records.append(rec)
        reg.write_text(json.dumps(records, indent=2))
        return {"recorded": rec, "total": len(records),
                "note": "manifest is now stale — refresh and re-copy"}

    # -- human-side helpers ------------------------------------------------
    def grant(self, op: str) -> str:
        if op != "rehearse":
            raise ValueError(f"unknown gated op '{op}' (valid: rehearse)")
        self.state.init()
        token = self.state.approvals / f"{op}.token"
        token.touch(mode=0o600)
        return str(token)

    def attest(self, location: str) -> dict:
        """HUMAN records that one offline copy exists at `location` and
        matches the current manifest. Attestations reset whenever the
        manifest is refreshed."""
        self._load_manifest()
        records = []
        if self.attestations_file.exists():
            records = json.loads(self.attestations_file.read_text())
        records = [a for a in records if a["location"] != location]
        records.append({"location": location,
                        "attested_at": self._clock().isoformat()})
        self.attestations_file.write_text(json.dumps(records, indent=2))
        return {"attested": location, "recorded": len(records)}
