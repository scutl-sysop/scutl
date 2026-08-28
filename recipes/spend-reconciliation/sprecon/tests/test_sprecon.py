"""sprecon component tests (recipe sprc rev 1, cst-r0vz).

The twin here is a FakeChain over hand-authored book files — the same
file formats the live recipes write (spend.log per scutl_signer,
earnings.log per scutl_pserv, instances.log per scutl_prov), authored
directly so the audit never imports the code it audits (matching the
component's own design point)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scutl_sprecon.core import Reconciler, ApprovalRequired

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
ADDR = "0xAAAA0000000000000000000000000000000000AA"
MERCHANT = "0xBBBB0000000000000000000000000000000000BB"
FUNDER = "0xCCCC0000000000000000000000000000000000CC"


class FakeChain:
    def __init__(self, transfers=(), balance="0", statuses=None, head=100):
        self._transfers = list(transfers)
        self.balance = Decimal(balance)
        self.statuses = statuses or {}
        self.head = head

    def head_block(self):
        return self.head

    def usdc_balance(self, address):
        return self.balance

    def tx_status(self, tx_hash):
        return self.statuses.get(tx_hash, "pending")

    def transfers(self, address, from_block, to_block=None):
        return [dict(t) for t in self._transfers
                if from_block <= t["block"] <= (to_block or self.head)]


def transfer(direction, amount, tx, block=10, counterparty=MERCHANT):
    return {"direction": direction, "counterparty": counterparty,
            "amount": Decimal(amount), "tx": tx, "block": block}


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        json.dumps(r, separators=(",", ":")) + "\n" for r in records))


def settled(pid, amount, tx, ts=None, to=MERCHANT):
    return {"ts": (ts or NOW - timedelta(hours=1)).isoformat(),
            "payment_id": pid, "to": to, "amount": amount, "tx": tx,
            "chain_status": "confirmed" if tx else "no-tx",
            "status": "settled"}


def reservation(pid, amount, valid_before=None, to=MERCHANT, ts=None):
    return {"ts": (ts or NOW - timedelta(hours=1)).isoformat(),
            "payment_id": pid, "to": to, "amount": amount,
            "status": "authorized",
            "valid_before": (valid_before or NOW + timedelta(minutes=10)
                             ).timestamp()}


@pytest.fixture
def wallet(tmp_path):
    d = tmp_path / "wallet"
    d.mkdir()
    (d / "keystore.json").write_text(
        json.dumps({"address_checksummed": ADDR, "crypto": {}}))
    write_jsonl(d / "spend.log", [])
    return d


def make(tmp_path, wallet, chain, **kw):
    return Reconciler(tmp_path / "sprecon", chain, wallet, now=NOW, **kw)


def baseline(r, balance="10"):
    r.grant_approval("rebaseline")
    r.baseline(Decimal(balance), 1, "owner")


# -- clean passes and conservation -------------------------------------

def test_clean_pass_closes_and_conserves(tmp_path, wallet):
    write_jsonl(wallet / "spend.log",
                [reservation("p1", "1.5"), settled("p1", "1.5", "0xt1")])
    chain = FakeChain([transfer("out", "1.5", "0xt1")], balance="8.5",
                      statuses={"0xt1": "confirmed"})
    r = make(tmp_path, wallet, chain)
    baseline(r)
    rep = r.reconcile()
    assert rep["conservation_ok"]
    assert rep["records_in"] == rep["records_categorized"] == 3
    assert rep["open_escalations"] == 0
    assert Decimal(rep["invariant"]["residue"]) == 0


def test_pass_leaves_audited_logs_byte_identical(tmp_path, wallet):
    write_jsonl(wallet / "spend.log", [settled("p1", "2", "0xt1")])
    before = hashlib.sha256((wallet / "spend.log").read_bytes()).hexdigest()
    chain = FakeChain([transfer("out", "2", "0xt1")], balance="8",
                      statuses={"0xt1": "confirmed"})
    r = make(tmp_path, wallet, chain)
    baseline(r)
    rep = r.reconcile()
    after = hashlib.sha256((wallet / "spend.log").read_bytes()).hexdigest()
    assert before == after
    assert not [f for f in rep["findings"]
                if f["category"] == "self-audit-write"]


def test_zero_amount_no_tx_is_honest(tmp_path, wallet):
    write_jsonl(wallet / "spend.log", [settled("p1", "0", "")])
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    baseline(r)
    rep = r.reconcile()
    assert rep["open_escalations"] == 0


# -- discovery: the drain ----------------------------------------------

def test_unlogged_transfer_out_escalates_verbatim(tmp_path, wallet):
    chain = FakeChain([transfer("out", "9.99", "0xdrain", block=42)],
                      balance="0.01")
    r = make(tmp_path, wallet, chain)
    baseline(r)
    rep = r.reconcile()
    f = [f for f in rep["findings"]
         if f["category"] == "unlogged-transfer-out"]
    assert len(f) == 1 and f[0]["state"] == "escalated"
    assert f[0]["evidence"]["tx"] == "0xdrain"
    assert f[0]["evidence"]["block"] == 42


def test_drain_not_re_escalated_on_second_pass(tmp_path, wallet):
    chain = FakeChain([transfer("out", "5", "0xdrain")], balance="5")
    r = make(tmp_path, wallet, chain)
    rep1 = r.reconcile()
    rep2 = r.reconcile()
    raised = [f for f in r.read_findings_raw()
              if f.get("category") == "unlogged-transfer-out"]
    assert len(raised) == 1
    assert any(f.get("prior") for f in rep2["findings"]
               if f["category"] == "unlogged-transfer-out")


# -- the by-design own-books case --------------------------------------

def test_unrecorded_merchant_settle_joins_not_drain(tmp_path, wallet):
    write_jsonl(wallet / "spend.log",
                [reservation("p1", "3", valid_before=NOW -
                             timedelta(minutes=5))])
    chain = FakeChain([transfer("out", "3", "0xlate")], balance="7")
    r = make(tmp_path, wallet, chain)
    rep = r.reconcile()
    cats = [f["category"] for f in rep["findings"]]
    assert "unrecorded-merchant-settle" in cats
    assert "unlogged-transfer-out" not in cats


def test_live_and_expired_reservations_are_honest(tmp_path, wallet):
    write_jsonl(wallet / "spend.log", [
        reservation("p1", "1"),
        reservation("p2", "2", valid_before=NOW - timedelta(hours=2))])
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    rep = r.reconcile()
    by_cat = {f["category"]: f for f in rep["findings"]}
    assert by_cat["live-reservation"]["state"] == "explained"
    assert by_cat["expired-reservation"]["state"] == "explained"
    assert rep["open_escalations"] == 0


# -- settled vs chain status -------------------------------------------

def test_settled_tx_failed_past_deadline_escalates(tmp_path, wallet):
    write_jsonl(wallet / "spend.log",
                [settled("p1", "2", "0xbad", ts=NOW - timedelta(hours=30))])
    r = make(tmp_path, wallet,
             FakeChain(statuses={"0xbad": "failed"}, balance="10"))
    rep = r.reconcile()
    f = [f for f in rep["findings"] if f["category"] == "settled-tx-failed"]
    assert f and f[0]["state"] == "escalated"


def test_pending_inside_deadline_rides_honest(tmp_path, wallet):
    write_jsonl(wallet / "spend.log",
                [settled("p1", "2", "0xslow", ts=NOW - timedelta(hours=1))])
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    rep = r.reconcile()
    f = [f for f in rep["findings"] if f["category"] == "pending-tx"]
    assert f and f[0]["state"] == "explained"
    assert rep["open_escalations"] == 0


def test_pending_past_deadline_escalates(tmp_path, wallet):
    write_jsonl(wallet / "spend.log",
                [settled("p1", "2", "0xslow", ts=NOW - timedelta(hours=30))])
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    rep = r.reconcile()
    assert [f for f in rep["findings"]
            if f["category"] == "pending-past-deadline"
            and f["state"] == "escalated"]


# -- tamper evidence ----------------------------------------------------

def test_log_prefix_tamper_escalates_with_digests(tmp_path, wallet):
    write_jsonl(wallet / "spend.log", [settled("p1", "1", "0xt1")])
    chain = FakeChain([transfer("out", "1", "0xt1")], balance="9",
                      statuses={"0xt1": "confirmed"})
    r = make(tmp_path, wallet, chain)
    r.reconcile()
    # mutate the already-reconciled line
    write_jsonl(wallet / "spend.log", [settled("p1", "0.01", "0xt1")])
    rep = r.reconcile()
    f = [f for f in rep["findings"] if f["category"] == "log-prefix-tampered"]
    assert f and f[0]["state"] == "escalated"
    ev = f[0]["evidence"]
    assert ev["checkpoint_digest"] != ev["current_digest"]


# -- earnings leg -------------------------------------------------------

def test_earnings_match_and_close_invariant(tmp_path, wallet):
    pserv = tmp_path / "pserv"
    write_jsonl(pserv / "earnings.log",
                [{"ts": NOW.isoformat(), "amount": "4", "tx": "0xin"}])
    chain = FakeChain([transfer("in", "4", "0xin", counterparty=MERCHANT)],
                      balance="14")
    r = make(tmp_path, wallet, chain, pserv_dir=pserv)
    baseline(r)
    rep = r.reconcile()
    assert rep["open_escalations"] == 0
    assert Decimal(rep["invariant"]["residue"]) == 0


def test_earnings_unconfirmed_escalates(tmp_path, wallet):
    pserv = tmp_path / "pserv"
    write_jsonl(pserv / "earnings.log",
                [{"ts": NOW.isoformat(), "amount": "4", "tx": "0xghost"}])
    r = make(tmp_path, wallet, FakeChain(balance="10"), pserv_dir=pserv)
    rep = r.reconcile()
    assert [f for f in rep["findings"]
            if f["category"] == "earnings-unconfirmed"
            and f["state"] == "escalated"]


# -- deposits and funding ----------------------------------------------

def test_attested_funding_explains_deposit(tmp_path, wallet):
    chain = FakeChain([transfer("in", "25", "0xfund",
                                counterparty=FUNDER)], balance="35")
    r = make(tmp_path, wallet, chain)
    write_jsonl(r.funding_log, [{"ts": NOW.isoformat(), "amount": "25",
                                 "tx": "0xfund", "attestor": "owner"}])
    baseline(r)
    rep = r.reconcile()
    assert [f for f in rep["findings"] if f["category"] == "attested-funding"]
    assert rep["open_escalations"] == 0
    assert Decimal(rep["invariant"]["residue"]) == 0


def test_unattested_deposit_escalates(tmp_path, wallet):
    chain = FakeChain([transfer("in", "25", "0xmystery",
                                counterparty=FUNDER)], balance="35")
    r = make(tmp_path, wallet, chain)
    rep = r.reconcile()
    assert [f for f in rep["findings"]
            if f["category"] == "unattested-deposit"
            and f["state"] == "escalated"]


# -- billing leg --------------------------------------------------------

def prov_with(tmp_path, events):
    prov = tmp_path / "prov"
    write_jsonl(prov / "instances.log", events)
    return prov


def test_foreign_invoice_line_escalates(tmp_path, wallet):
    prov = prov_with(tmp_path, [])
    r = make(tmp_path, wallet, FakeChain(balance="10"), prov_dir=prov)
    rep = r.reconcile(billing=[{"instance_id": "i-foreign", "amount": "3"}])
    assert [f for f in rep["findings"]
            if f["category"] == "foreign-invoice-line"
            and f["state"] == "escalated"]


def test_billing_after_destroy_escalates(tmp_path, wallet):
    prov = prov_with(tmp_path, [
        {"ts": (NOW - timedelta(days=3)).isoformat(), "event": "created",
         "id": "i-1", "hourly_usd": "0.02"},
        {"ts": (NOW - timedelta(days=2)).isoformat(), "event": "destroyed",
         "id": "i-1"}])
    r = make(tmp_path, wallet, FakeChain(balance="10"), prov_dir=prov)
    rep = r.reconcile(billing=[{
        "instance_id": "i-1", "hourly_usd": "0.02",
        "period_start": (NOW - timedelta(days=1)).isoformat()}])
    assert [f for f in rep["findings"]
            if f["category"] == "billing-after-destroy"]


def test_estimate_vs_actual_is_honest(tmp_path, wallet):
    prov = prov_with(tmp_path, [
        {"ts": NOW.isoformat(), "event": "created", "id": "i-1",
         "hourly_usd": "0.0200"}])
    r = make(tmp_path, wallet, FakeChain(balance="10"), prov_dir=prov)
    rep = r.reconcile(billing=[{"instance_id": "i-1",
                                "hourly_usd": "0.0192"}])
    f = [f for f in rep["findings"] if f["category"] == "estimate-vs-actual"]
    assert f and f[0]["state"] == "explained"
    assert rep["open_escalations"] == 0


def test_statement_absent_is_never_clean(tmp_path, wallet):
    prov = prov_with(tmp_path, [])
    r = make(tmp_path, wallet, FakeChain(balance="10"), prov_dir=prov)
    rep = r.reconcile()
    assert rep["legs"]["billing"] == "absent"
    assert [f for f in rep["findings"]
            if f["category"] == "statement-absent"
            and f["state"] == "escalated"]


# -- invariant residue --------------------------------------------------

def test_residue_escalates_not_absorbed(tmp_path, wallet):
    r = make(tmp_path, wallet, FakeChain(balance="9.37"))
    baseline(r, "10")
    rep = r.reconcile()
    f = [f for f in rep["findings"] if f["category"] == "balance-residue"]
    assert f and f[0]["state"] == "escalated"
    assert f[0]["evidence"]["residue"] == "-0.63"


# -- gates --------------------------------------------------------------

def test_resolve_escalated_requires_token(tmp_path, wallet):
    chain = FakeChain([transfer("out", "5", "0xdrain")], balance="5")
    r = make(tmp_path, wallet, chain)
    rep = r.reconcile()
    fid = [f for f in rep["findings"]
           if f["category"] == "unlogged-transfer-out"][0]["id"]
    with pytest.raises(ApprovalRequired):
        r.resolve(fid, "owner confirmed: authorized sweep")
    r.grant_approval("resolve")
    rec = r.resolve(fid, "owner confirmed: authorized sweep")
    assert rec["state"] == "resolved"
    assert not r.findings(state="escalated")


def test_resolve_explained_needs_no_token(tmp_path, wallet):
    write_jsonl(wallet / "spend.log", [reservation("p1", "1")])
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    rep = r.reconcile()
    fid = rep["findings"][0]["id"]
    assert r.resolve(fid, "noted")["state"] == "resolved"


def test_rebaseline_is_tokened_and_records_old_new_why(tmp_path, wallet):
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    baseline(r, "10")
    with pytest.raises(ApprovalRequired):
        r.rebaseline(Decimal("12"), 50, "owner topped up off-record")
    r.grant_approval("rebaseline")
    rec = r.rebaseline(Decimal("12"), 50, "owner topped up off-record")
    ev = rec["evidence"]
    assert ev["opening_balance"] == "12"
    assert ev["old_anchor"]["opening_balance"] == "10"
    assert ev["reason"] == "owner topped up off-record"


def test_baseline_only_once(tmp_path, wallet):
    r = make(tmp_path, wallet, FakeChain(balance="10"))
    baseline(r, "10")
    r.grant_approval("rebaseline")
    with pytest.raises(ValueError):
        r.baseline(Decimal("11"), 2, "owner")


# -- status -------------------------------------------------------------

def test_status_reports_checkpoint_and_open_escalations(tmp_path, wallet):
    chain = FakeChain([transfer("out", "5", "0xdrain")], balance="5",
                      head=77)
    r = make(tmp_path, wallet, chain)
    r.reconcile()
    st = r.status()
    assert st["last_reconciled_block"] == 77
    assert st["open_escalations"] == {"unlogged-transfer-out": 1}
    assert st["books"]["spend"]["present"]
