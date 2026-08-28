"""Acceptance tests for the idbackup component (recipe #3, idbr rev 1).

Everything runs against a tmp state root with a cheap (pbkdf2) keystore
and an injectable clock. The design points under test: the manifest
digests everything (kek included) and never carries contents; verify is
secret-free and names each failure mode precisely; the rehearsal is a
TRUE restore into a fresh dir that must sign, reconciles counters, and
provably never writes into the live state dir.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from eth_account import Account

from scutl_idbackup.core import (ApprovalRequired, Archivist, Panicked,
                                 Tombstoned, UnverifiedBackup)

KEK = "aa" * 32
KEY = "0x" + "11" * 32
OTHER_KEY = "0x" + "22" * 32


class FakeClock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def write_keystore(path: Path, key: str, kek: str) -> str:
    acct = Account.from_key(key)
    doc = Account.encrypt(key, kek, kdf="pbkdf2", iterations=100)
    doc["address_checksummed"] = acct.address
    path.write_text(json.dumps(doc))
    return acct.address


def make_identity(root: Path, key: str = KEY, kek: str = KEK,
                  spends: int = 2) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / "approvals").mkdir(exist_ok=True)
    address = write_keystore(root / "keystore.json", key, kek)
    (root / "kek").write_text(kek)
    (root / "network.json").write_text(json.dumps({"network": "eip155:84532"}))
    (root / "caps.json").write_text(json.dumps(
        {"cap_per_tx": "0.25", "cap_daily": "1.00"}))
    lines = [json.dumps({"ts": f"2026-08-0{i+1}T00:00:00+00:00",
                         "payment_id": f"p{i}", "to": "0x" + "33" * 20,
                         "amount": "0.05", "status": "settled"})
             for i in range(spends)]
    (root / "spend.log").write_text("\n".join(lines) + "\n" if lines else "")
    return address


@pytest.fixture
def arch(tmp_path):
    def make(clock=None, **kw):
        make_identity(tmp_path / "state", **kw)
        return Archivist(state_root=tmp_path / "state",
                         clock=clock or FakeClock())
    return make


def copy_backup(a: Archivist, dest: Path) -> Path:
    dest.mkdir(exist_ok=True)
    man = json.loads(a.manifest_file.read_text())
    for name in man["artifacts"]:
        shutil.copyfile(a.root / name, dest / name)
    return dest


def manifested_backup(a: Archivist, tmp_path: Path) -> Path:
    a.manifest()
    return copy_backup(a, tmp_path / "offline")


# -- manifest ---------------------------------------------------------------

def test_manifest_digests_everything_kek_included(arch):
    a = arch()
    out = a.manifest()
    assert out["kek_digest_recorded"]
    man = json.loads(a.manifest_file.read_text())
    for name in ("keystore.json", "kek", "network.json", "caps.json",
                 "spend.log"):
        assert name in man["artifacts"], name
    assert man["counters"]["spend_settled_total"] == "0.10"
    assert man["counters"]["spend_lines"] == 2
    # digests, never contents: the kek's bytes appear nowhere
    assert KEK not in a.manifest_file.read_text()


def test_manifest_refresh_resets_attestations(arch):
    a = arch()
    a.manifest()
    a.attest("safe-A")
    assert a.status()["attestations"]["recorded"] == 1
    a.manifest()
    assert a.status()["attestations"]["recorded"] == 0


def test_manifest_without_identity_is_not_setup(tmp_path):
    a = Archivist(state_root=tmp_path / "empty", clock=FakeClock())
    with pytest.raises(FileNotFoundError):
        a.manifest()


# -- verify: each failure mode named precisely ------------------------------

def test_verify_ok_on_pristine_copy(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    r = a.verify(backup)
    assert r["verdict"] == "ok" and r["healthy"]
    assert not (r["missing"] or r["mismatched"] or r["extra"])


def test_verify_partial_names_the_missing_kek(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    (backup / "kek").unlink()
    r = a.verify(backup)
    assert r["verdict"] == "partial" and not r["healthy"]
    assert r["missing"] == ["kek"]


def test_verify_corrupt_on_flipped_byte(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    raw = bytearray((backup / "caps.json").read_bytes())
    raw[0] ^= 0xFF
    (backup / "caps.json").write_bytes(bytes(raw))
    r = a.verify(backup)
    assert r["verdict"] == "corrupt"
    assert r["mismatched"] == ["caps.json"]


def test_verify_foreign_on_wrong_wallet_copy(arch, tmp_path):
    a = arch()
    a.manifest()
    other_root = tmp_path / "other"
    make_identity(other_root, key=OTHER_KEY)
    b = Archivist(state_root=other_root, clock=FakeClock())
    backup = manifested_backup(b, tmp_path)
    r = a.verify(backup)
    assert r["verdict"] == "foreign"
    assert r["foreign"]["manifest_owner"] != r["foreign"]["backup_address"]


def test_verify_flags_planted_extra_file(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    (backup / "bonus.json").write_text("{}")
    r = a.verify(backup)
    assert r["verdict"] == "corrupt"
    assert r["extra"] == ["bonus.json"]


def test_verify_truncated_log_names_the_exact_delta(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    lines = (backup / "spend.log").read_text().splitlines()
    (backup / "spend.log").write_text(lines[0] + "\n")
    r = a.verify(backup)
    assert r["verdict"] == "corrupt"
    assert r["truncated"] == [{"log": "spend.log", "lines_expected": 2,
                               "lines_present": 1}]
    assert r["counter_delta"]["delta"] == "0.05"


def test_verify_stale_on_live_drift(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    with open(a.state.spend_log, "a") as f:
        f.write(json.dumps({"ts": "2026-08-27T00:00:00+00:00",
                            "payment_id": "p9", "to": "0x" + "33" * 20,
                            "amount": "0.01", "status": "settled"}) + "\n")
    r = a.verify(backup)
    assert r["verdict"] == "stale"
    assert "spend.log" in r["drifted_since_manifest"]


def test_verify_stale_on_age(arch, tmp_path):
    clock = FakeClock()
    a = arch(clock=clock)
    backup = manifested_backup(a, tmp_path)
    clock.advance(days=31)
    r = a.verify(backup)
    assert r["verdict"] == "stale" and r["manifest_aged_out"]


def test_verify_is_never_healthy_over_a_tombstone(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    a.state.tombstone.write_text(json.dumps(
        {"address": "0xdead", "revoked_at": "2026-08-28"}))
    r = a.verify(backup)
    assert r["tombstoned"] and not r["healthy"]


def test_verify_is_secret_free_and_ungated(arch, tmp_path):
    # no approval token exists, and the output carries no kek bytes
    a = arch()
    backup = manifested_backup(a, tmp_path)
    r = a.verify(backup)
    assert KEK not in json.dumps(r)


# -- rehearse: the true restore ---------------------------------------------

def test_rehearse_requires_approval(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    with pytest.raises(ApprovalRequired):
        a.rehearse(backup)


def test_rehearse_passes_and_live_stays_untouched(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    before = {p.name: p.read_bytes() for p in a.root.iterdir() if p.is_file()}
    a.grant("rehearse")
    r = a.rehearse(backup)
    assert r["rehearsal_passed"] and r["signature_proven"]
    assert Decimal(r["counter_delta"]["delta"]) == 0
    assert r["live_untouched"]
    after = {p.name: p.read_bytes() for p in a.root.iterdir()
             if p.is_file() and p.name != "rehearsal.json"}
    assert after == before
    # the approval token was consumed: one token, one rehearsal
    with pytest.raises(ApprovalRequired):
        a.rehearse(backup)


def test_rehearse_refuses_unverified_copy(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    (backup / "kek").unlink()
    a.grant("rehearse")
    with pytest.raises(UnverifiedBackup):
        a.rehearse(backup)


def test_rehearse_kek_keystore_mismatch_fails_clean(arch, tmp_path):
    # the live pair itself is mismatched (human error at ceremony time):
    # digests all verify, decrypt fails, nothing written to live
    a = arch()
    (a.root / "kek").write_text("bb" * 32)
    backup = manifested_backup(a, tmp_path)
    before = {p.name: p.read_bytes() for p in a.root.iterdir() if p.is_file()}
    a.grant("rehearse")
    with pytest.raises(ValueError):
        a.rehearse(backup)
    after = {p.name: p.read_bytes() for p in a.root.iterdir() if p.is_file()}
    assert after == before


def test_rehearse_truncated_log_reports_the_delta(arch, tmp_path):
    # amnesia-policy reconcile-or-approve: only-truncation still rehearses,
    # and the delta comes back with numbers, not a pass
    a = arch()
    backup = manifested_backup(a, tmp_path)
    lines = (backup / "spend.log").read_text().splitlines()
    (backup / "spend.log").write_text(lines[0] + "\n")
    a.grant("rehearse")
    r = a.rehearse(backup)
    assert not r["rehearsal_passed"]
    assert r["counter_delta"]["delta"] == "0.05"
    assert "re-arms" in r["amnesia_warning"]


def test_rehearse_refuses_tombstone_and_panic(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    a.grant("rehearse")
    (a.root / "panic.json").write_text(json.dumps(
        {"panicked_at": "2026-08-28", "reason": "test"}))
    with pytest.raises(Panicked):
        a.rehearse(backup)
    (a.root / "panic.json").unlink()
    a.state.tombstone.write_text(json.dumps(
        {"address": "0xdead", "revoked_at": "2026-08-28"}))
    with pytest.raises(Tombstoned):
        a.rehearse(backup)


def test_rehearse_output_carries_no_key_material(arch, tmp_path):
    a = arch()
    backup = manifested_backup(a, tmp_path)
    a.grant("rehearse")
    r = a.rehearse(backup)
    blob = json.dumps(r)
    assert KEK not in blob and KEY[2:] not in blob


# -- owned resources --------------------------------------------------------

def test_owned_resource_recorded_and_rehearsed(arch, tmp_path):
    a = arch()
    out = a.record_owned_resource("agentmail", "scutl-star@agentmail.to",
                                  price="0.30", evidence="0x" + "cd" * 32)
    assert out["total"] == 1
    backup = manifested_backup(a, tmp_path)
    a.grant("rehearse")
    r = a.rehearse(backup)
    assert r["rehearsal_passed"]
    assert r["owned_resources"] == [{
        "resource": "scutl-star@agentmail.to", "provider": "agentmail",
        "address_matches": True}]


def test_rehearse_probe_denial_fails_the_rehearsal(arch, tmp_path):
    a = arch()
    a.record_owned_resource("agentmail", "scutl-star@agentmail.to")
    backup = manifested_backup(a, tmp_path)
    a.grant("rehearse")
    r = a.rehearse(backup, probe=lambda addr, rec: False)
    assert not r["rehearsal_passed"]
    assert r["owned_resources"][0]["probed_owned"] is False


# -- status -----------------------------------------------------------------

def test_status_reports_health_and_staleness(arch, tmp_path):
    clock = FakeClock()
    a = arch(clock=clock)
    s = a.status()
    assert s["configured"] and s["manifest"] is None
    backup = manifested_backup(a, tmp_path)
    a.attest("safe-A")
    a.attest("safe-B")
    a.grant("rehearse")
    a.rehearse(backup)
    s = a.status()
    assert not s["stale"]
    assert s["attestations"]["recorded"] == 2
    assert s["last_rehearsal"]["rehearsal_passed"]
    clock.advance(days=31)
    assert a.status()["stale"]
