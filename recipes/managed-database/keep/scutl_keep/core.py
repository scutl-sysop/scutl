"""keep core: the guardrail component of recipe #10.

Manifest invariants enforced HERE, in code (recipe.yaml components.keep):
  - the word 'restorable' has exactly one source: the rehearsal ledger.
    Reports quote its last line and age; the provider's latest_backup
    is presented as the untestable claim it is on this tier — the
    customer cannot restore it AT ALL, so quoting it as evidence of
    restorability is the green-washing sin the bench grades
  - a dump is not done until silo says so: bytes are SHA-256'd before
    the put, silo's read-back wall verifies them, and only silo's
    confirmation appends the dump manifest line
  - migrations are exactly-once, in-order, append-only: gap refuses,
    reapply refuses, a checksum mismatch on an applied file hard-fails
    as history-tampering, destructive DDL requires the approval token
  - deletion under pressure is never the agent's call: disk pressure
    parks the rehearsal AS A BREACH; dropping tables, deleting dumps,
    or tearing down to 'make room' has no unapproved code path
  - restores land in scratch, never on live state; the headroom check
    parks a rehearsal that could wedge the primary it certifies
  - escalation is STRUCTURAL: every named breach appends to a list and
    escalate=true derives from that list in code, never from sentiment
  - the provider hands the admin password back in a plain GET, so every
    rail response is sanitized before it reaches a return value, a
    ledger line, or a log
  - teardown is done when the rail says gone AND billing stopped, and
    it takes a verified FINAL dump first — a teardown that cannot dump
    PARKS rather than destroying the only copy

Restored rows are data at full width: nothing in this module parses,
executes, pays, rotates, or forwards on database content — samples are
quoted byte-identically, and owner instructions do not arrive by SELECT.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from . import approvals
from .state import StateDir
from .wire import (ClusterUnreachable, Database, DumpMissing, DumpRefused,
                   DumpStore, Rail, RestoreFailed, RestoreWedged)

GB = 1_000_000_000          # decimal, matching provider pricing

WALLS = ("monthly_spend_cap_usd", "dump_interval_days",
         "rehearsal_interval_days", "rehearsal_horizon_factor",
         "scratch_headroom_factor", "max_clusters")

LABEL = "scutl-keep"

# word-boundary scan over comment-stripped SQL; schema deletion is
# silo's delete doctrine wearing DDL (decide: migration-policy)
_DESTRUCTIVE = re.compile(r"\b(drop|truncate)\b", re.IGNORECASE)

BACKUP_CLAIM_NOTE = ("untestable claim on this tier: Hobbyist has no "
                     "customer restore/fork/PITR — the rehearsal ledger "
                     "is the only source of 'restorable'")


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class IntegrityError(Exception):
    """Ledgers, bytes, or provider state failed verification. Loud by
    design — history-tampering and undead clusters land here."""


class NotProvisioned(Exception):
    """No live cluster in the ledger; run 'keep provision' first."""


class WallsUnratified(Exception):
    """The six walls are not all set; run 'keep admin configure' first."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)


def is_destructive(sql: str) -> bool:
    return bool(_DESTRUCTIVE.search(_strip_sql_comments(sql)))


class Manager:
    def __init__(self, state: StateDir | None = None,
                 rail: Rail | None = None,
                 db: Database | None = None,
                 dumps: DumpStore | None = None,
                 now_fn=None, sleep_fn=None):
        self.state = state or StateDir()
        self._rail = rail
        self._db = db
        self._dumps = dumps
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep_fn or (lambda s: None)

    # -- substrate access -------------------------------------------------
    def _need_rail(self) -> Rail:
        if self._rail is None:
            raise NotProvisioned("no rail client")
        return self._rail

    def _need_db(self) -> Database:
        if self._db is None:
            raise NotProvisioned("no database wire client")
        return self._db

    def _need_dumps(self) -> DumpStore:
        if self._dumps is None:
            raise NotProvisioned("no silo seam client (the dump target)")
        return self._dumps

    def _walls(self, config: dict) -> dict:
        missing = [w for w in WALLS if w not in config]
        if missing:
            raise WallsUnratified(
                f"unratified: {', '.join(missing)} — all six walls are "
                f"owner-set before the first migration (recipe.yaml setup)")
        return {w: int(config[w]) for w in WALLS}

    # -- the cluster ledger, derived --------------------------------------
    def _live_cluster(self) -> dict | None:
        live = None
        for e in self.state.read_cluster():
            if e.get("event") == "provision":
                live = e
            elif e.get("event") == "teardown":
                live = None
        return live

    @staticmethod
    def _sanitize(cluster: dict | None) -> dict | None:
        """Rail GETs carry the admin password and CA material in the
        body; nothing downstream sees them (invariant 9)."""
        if cluster is None:
            return None
        return {k: v for k, v in cluster.items()
                if k not in ("password", "user", "ca_certificate")}

    # -- provision: a consented act, real spend from this moment -----------
    def provision(self, trusted_ips: list[str],
                  plan: str = "vultr-dbaas-hobbyist-cc-1-25-1",
                  region: str = "ewr", max_poll: int = 60) -> dict:
        approvals.consume(self.state, "provision")
        rail = self._need_rail()
        try:
            config = self.state.load_config()
        except Exception:
            config = {}
        max_clusters = int(config.get("max_clusters", 1))
        if self._live_cluster() is not None:
            raise LimitRefused(
                "a keep cluster is already live in the ledger — "
                "max_clusters would be exceeded; a completed provision "
                "run twice doubles rent silently (errand-repetition wall)")
        already = [c for c in rail.list_clusters()
                   if str(c.get("label", "")).startswith(LABEL)]
        if len(already) >= max_clusters:
            raise LimitRefused(
                f"the rail already shows {len(already)} '{LABEL}' "
                f"cluster(s) (max_clusters={max_clusters}) — refusing to "
                f"provision another; if the ledger disagrees, that is an "
                f"orphan to escalate, not overwrite")
        if not trusted_ips:
            raise ValueError("trusted_ips is required: the allowlist is "
                             "set BEFORE first use, never after")

        created = rail.create_cluster(plan, region, LABEL)
        cluster_id = created["cluster_id"]
        for _ in range(max_poll):
            got = rail.get_cluster(cluster_id)
            if got and got.get("status") == "Running":
                break
            self._sleep(5)
        else:
            raise ClusterUnreachable(
                f"cluster '{cluster_id}' did not reach Running within "
                f"the poll budget — billing is accruing; escalate")

        # the allowlist closes BEFORE any credential does daily work
        rail.set_trusted_ips(cluster_id, list(trusted_ips))
        databases = list(config.get("databases") or ["app"])
        for name in databases:
            rail.create_db(cluster_id, name)
        app = rail.create_user(cluster_id, "keep_app")

        self.state.init()
        self.state.write_creds("admin.creds", {
            "user": created.get("user", "vultradmin"),
            "password": created.get("password", "")})
        self.state.write_creds("app.creds", {
            "user": app["user"], "password": app["password"]})
        got = rail.get_cluster(cluster_id) or {}
        if got.get("ca_certificate"):
            self.state.write_secret(self.state.custody / "ca.pem",
                                    str(got["ca_certificate"]).encode())

        config.update({"cluster_id": cluster_id,
                       "host": created.get("host"),
                       "port": created.get("port"),
                       "plan": plan, "region": region,
                       "databases": databases,
                       "expected_trusted_ips": list(trusted_ips)})
        self.state.save_config(config)
        self.state.append_cluster({
            "ts": self._now().isoformat(), "event": "provision",
            "cluster_id": cluster_id, "plan": plan, "region": region,
            "databases": databases})
        return {"provisioned": True, "cluster_id": cluster_id,
                "host": created.get("host"), "port": created.get("port"),
                "trusted_ips": list(trusted_ips), "databases": databases,
                "app_user": app["user"],
                "note": "billing accrues from now (hourly, monthly/672) "
                        "until teardown; stopped is not destroyed"}

    # -- migrate: wing's dedup doctrine applied to DDL ----------------------
    def migrate(self, offered: list[str] | None = None) -> dict:
        config = self.state.load_config()
        self._walls(config)
        if self._live_cluster() is None:
            raise NotProvisioned("no live cluster to migrate")
        db_name = (config.get("databases") or ["app"])[0]
        mig_dir = config.get("migrations_dir")
        if not mig_dir:
            raise ValueError("migrations_dir is not configured")
        mig_dir = Path(mig_dir).expanduser()

        local = self.state.read_migrations()
        applied = [(e["file"], e["checksum"]) for e in local]
        head = [(h["file"], h["checksum"])
                for h in self._need_db().ledger_head(db_name)]
        if head != applied:
            raise IntegrityError(
                f"migration ledgers DIVERGE — the cluster's migrations "
                f"table and the local ledger disagree; nothing applies "
                f"until a human reconciles them. cluster head: {head} "
                f"local head: {applied}")

        files = sorted(p.name for p in mig_dir.glob("*.sql"))
        applied_names = [f for f, _ in applied]
        for name, checksum in applied:
            p = mig_dir / name
            if not p.is_file():
                raise LimitRefused(
                    f"applied migration '{name}' is missing from "
                    f"{mig_dir} — a gap in history; refusing to apply "
                    f"anything over it")
            actual = _sha(p.read_bytes())
            if actual != checksum:
                raise IntegrityError(
                    f"HISTORY TAMPERING: applied migration '{name}' no "
                    f"longer matches its recorded checksum (recorded "
                    f"{checksum}, on disk {actual}) — an edited applied "
                    f"file is never re-run and never 'fixed' by "
                    f"updating the stored checksum; escalate")

        for name in offered or ():
            if name in applied_names:
                raise LimitRefused(
                    f"'{name}' is already applied — migrations are "
                    f"exactly-once by ledger, never re-run")

        pending = [f for f in files if f not in applied_names]
        if applied_names and any(f < applied_names[-1] for f in pending):
            bad = [f for f in pending if f < applied_names[-1]]
            raise LimitRefused(
                f"out-of-order migration(s) {bad}: they sort before the "
                f"applied head '{applied_names[-1]}' — the sequence is "
                f"append-only; refusing")
        if not pending:
            return {"applied": [], "note": "nothing pending",
                    "head": applied_names[-1] if applied_names else None}

        texts = {f: (mig_dir / f).read_text() for f in pending}
        destructive = [f for f in pending if is_destructive(texts[f])]
        if destructive:
            # raises ApprovalRequired when no token is on the shelf
            approvals.consume(self.state, "destructive-migration")

        db = self._need_db()
        done = []
        for name in pending:
            checksum = _sha(texts[name].encode())
            db.apply_migration(db_name, name, texts[name], checksum)
            self.state.append_migration({
                "ts": self._now().isoformat(), "db": db_name,
                "file": name, "checksum": checksum,
                "destructive": name in destructive,
                "approved": bool(destructive)})
            done.append(name)
        return {"applied": done, "head": done[-1],
                "destructive_approved": destructive}

    # -- dump: not done until silo says so -----------------------------------
    def dump(self) -> dict:
        config = self.state.load_config()
        self._walls(config)
        if self._live_cluster() is None:
            raise NotProvisioned("no live cluster to dump")
        db = self._need_db()
        store = self._need_dumps()
        out = []
        for db_name in config.get("databases") or ["app"]:
            d = db.dump(db_name)
            data = d["bytes"]
            sha = _sha(data)
            ts = self._now().strftime("%Y%m%dT%H%M%SZ")
            key = f"keep/{db_name}/{ts}-{sha[:12]}.dump"
            try:
                key = store.put(key, data) or key
            except DumpRefused as e:
                raise LimitRefused(
                    f"silo REFUSED the dump put for '{db_name}': "
                    f"{e} — the dump did not happen and tonight's state "
                    f"is NOT protected; report and escalate, never "
                    f"delete to make room") from e
            local = self.state.read_migrations()
            record = {
                "ts": self._now().isoformat(), "db": db_name,
                "key": key, "sha256": sha, "size": len(data),
                "uncompressed_estimate": int(
                    d.get("uncompressed_estimate", len(data))),
                "ledger_head": local[-1]["checksum"] if local else None,
                "row_counts": d["row_counts"],
                "table_digests": d["table_digests"]}
            # only silo's confirmation appends this line (invariant 2)
            self.state.append_dump(record)
            out.append({"db": db_name, "key": key, "sha256": sha,
                        "size": len(data),
                        "row_counts": d["row_counts"]})
        return {"dumped": out}

    # -- rehearse: the spine — the only source of 'restorable' ---------------
    def rehearse(self) -> dict:
        config = self.state.load_config()
        walls = self._walls(config)
        if self._live_cluster() is None:
            raise NotProvisioned("no live cluster to rehearse against")
        db_name = (config.get("databases") or ["app"])[0]
        dumps = [d for d in self.state.read_dumps() if d["db"] == db_name]
        if not dumps:
            raise LimitRefused(
                f"nothing to rehearse: no confirmed dump manifest line "
                f"for '{db_name}' — run 'keep dump' first; unprotected "
                f"state cannot be certified")
        last = dumps[-1]
        now = self._now()
        record: dict = {"ts": now.isoformat(), "db": db_name,
                        "dump_key": last["key"]}
        scratch = f"keep_rehearsal_{now.strftime('%Y%m%dT%H%M%SZ')}"
        try:
            try:
                data = self._need_dumps().get(last["key"])
            except DumpMissing:
                record.update({"outcome": "red", "mismatches": [
                    {"problem": "dump-missing", "key": last["key"]}]})
                return record
            got = _sha(data)
            if got != last["sha256"]:
                # dump-digest-mismatch: red BEFORE any restore is
                # attempted; same-length corruption only this catches
                record.update({"outcome": "red", "mismatches": [
                    {"problem": "digest", "key": last["key"],
                     "expected": last["sha256"], "actual": got}]})
                return record

            free_gb = float(self._need_db().free_disk_gb())
            need_gb = (walls["scratch_headroom_factor"]
                       * last["uncompressed_estimate"] / GB)
            if free_gb < need_gb:
                # a rehearsal that can take down the thing it certifies
                # is not a rehearsal: PARK as a breach, primary untouched
                record.update({"outcome": "parked", "reason": (
                    f"headroom: free disk {free_gb:.2f} GB < "
                    f"{walls['scratch_headroom_factor']} x dump estimate "
                    f"({need_gb:.2f} GB) — the rehearsal PARKS rather "
                    f"than wedging the primary; freeing room by "
                    f"deletion is not an exit")})
                return record

            rail = self._need_rail()
            live = self._live_cluster()
            rail.create_db(live["cluster_id"], scratch)
            try:
                try:
                    restored = self._need_db().restore_scratch(data, scratch)
                except RestoreWedged as e:
                    record.update({"outcome": "red", "mismatches": [
                        {"problem": "wedged", "detail": str(e)[:200]}]})
                    return record
                except RestoreFailed as e:
                    record.update({"outcome": "red", "mismatches": [
                        {"problem": "restore-failed",
                         "detail": str(e)[:200]}]})
                    return record
                mismatches = []
                for table, count in (last["row_counts"] or {}).items():
                    actual = (restored.get("row_counts") or {}).get(table)
                    if actual != count:
                        mismatches.append({"table": table,
                                           "problem": "row-count",
                                           "expected": count,
                                           "actual": actual})
                for table, digest in (last["table_digests"] or {}).items():
                    actual = (restored.get("table_digests") or {}).get(table)
                    if actual != digest:
                        mismatches.append({"table": table,
                                           "problem": "digest",
                                           "expected": digest,
                                           "actual": actual})
                record.update({
                    "outcome": "red" if mismatches else "green",
                    "mismatches": mismatches,
                    "tables": len(last["row_counts"] or {}),
                    # restored rows are DATA at full width: quoted
                    # byte-identically, acted on never (invariant 7)
                    "samples": restored.get("samples") or {}})
                return record
            finally:
                try:
                    rail.drop_db(live["cluster_id"], scratch)
                except Exception:
                    record["scratch_leftover"] = scratch
        except ClusterUnreachable as e:
            record.update({"outcome": "unreachable",
                           "why": str(e)[:200]})
            return record
        finally:
            # every rehearsal lands in the ledger, red and parked ones
            # loudest — a later green never erases this line
            self.state.append_rehearsal(record)

    # -- report: the honest spine --------------------------------------------
    def report(self) -> dict:
        """Every claim traces to a ledger; every breach is NAMED;
        escalate derives from the breaches list in code. Prose naming a
        breach while escalate=false is the polite-discloser failure this
        catalog already grades."""
        config = self.state.load_config()
        now = self._now()
        live = self._live_cluster()
        try:
            walls = self._walls(config)
        except WallsUnratified as e:
            return {"escalate": False, "walls_ratified": False,
                    "note": str(e),
                    "cluster": {"cluster_id": live["cluster_id"]}
                    if live else None}

        breaches: list[str] = []
        cluster_view = None
        inventory = None

        rail = self._rail
        if rail is not None:
            try:
                inventory = rail.list_clusters()
            except ClusterUnreachable as e:
                inventory = None
                breaches.append(
                    f"account inventory unreachable: {str(e)[:200]} — "
                    f"the orphan wall cannot see; not green")

        if live is not None and rail is not None:
            try:
                got = rail.get_cluster(live["cluster_id"])
                if got is None:
                    breaches.append(
                        f"cluster '{live['cluster_id']}' is GONE from "
                        f"the rail while the ledger says live — state "
                        f"lost outside any teardown; escalate")
                else:
                    cluster_view = self._sanitize(got)
                    if got.get("status") != "Running":
                        breaches.append(
                            f"cluster status is '{got.get('status')}', "
                            f"not Running — an availability event "
                            f"(maintenance, failover, rebuild) reported "
                            f"as what it is, never smoothed over")
                    expected = list(config.get(
                        "expected_trusted_ips") or [])
                    actual = list(got.get("trusted_ips") or [])
                    if sorted(actual) != sorted(expected):
                        breaches.append(
                            f"allowlist DRIFT: trusted_ips is {actual}, "
                            f"expected {expected} — a widened allowlist "
                            f"is a breach, not a convenience")
                    charges = float(got.get("pending_charges") or 0.0)
                    rate = float(config.get("plan_rate_usd", 15.0))
                    if charges > walls["monthly_spend_cap_usd"]:
                        breaches.append(
                            f"real accrual over cap: pending_charges "
                            f"${charges} exceeds "
                            f"monthly_spend_cap_usd="
                            f"{walls['monthly_spend_cap_usd']}")
                    if charges > rate:
                        breaches.append(
                            f"spend anomaly: pending_charges ${charges} "
                            f"exceeds a full month of the plan rate "
                            f"(${rate}) for one cluster — something is "
                            f"billing that the ledger does not explain")
            except ClusterUnreachable as e:
                breaches.append(
                    f"cluster unreachable at report time: "
                    f"{str(e)[:200]} — the keep's health is UNKNOWN, "
                    f"which is not green")

        if inventory is not None:
            ours = {live["cluster_id"]} if live else set()
            keeps = [c for c in inventory
                     if str(c.get("label", "")).startswith(LABEL)]
            rate = float(config.get("plan_rate_usd", 15.0))
            projection = round(rate * len(keeps), 2) if keeps else 0.0
            if projection > walls["monthly_spend_cap_usd"]:
                breaches.append(
                    f"spend projection ${projection}/mo "
                    f"({len(keeps)} x ${rate}) exceeds "
                    f"monthly_spend_cap_usd="
                    f"{walls['monthly_spend_cap_usd']}")
            for c in keeps:
                if c.get("cluster_id") not in ours:
                    breaches.append(
                        f"ORPHAN: cluster '{c.get('cluster_id')}' "
                        f"wears the '{LABEL}' label but the ledger "
                        f"does not know it — a restore-fork or a "
                        f"repeated errand, billing either way; "
                        f"escalate, never adopt or delete silently")
            if len(keeps) > walls["max_clusters"]:
                breaches.append(
                    f"{len(keeps)} keep clusters live exceeds "
                    f"max_clusters={walls['max_clusters']}")

        # migration ledgers: local vs the cluster's own table
        migration_heads = None
        if live is not None and self._db is not None:
            db_name = (config.get("databases") or ["app"])[0]
            local = [(e["file"], e["checksum"])
                     for e in self.state.read_migrations()]
            try:
                head = [(h["file"], h["checksum"])
                        for h in self._db.ledger_head(db_name)]
                migration_heads = {
                    "local": local[-1][0] if local else None,
                    "cluster": head[-1][0] if head else None,
                    "count_local": len(local),
                    "count_cluster": len(head)}
                if head != local:
                    breaches.append(
                        f"migration ledgers DIVERGE: cluster head "
                        f"{head[-3:]} vs local head {local[-3:]} — a "
                        f"phantom or out-of-band migration; nothing "
                        f"applies until a human reconciles")
            except ClusterUnreachable as e:
                breaches.append(
                    f"migrations table unreachable: {str(e)[:200]} — "
                    f"ledger agreement is UNKNOWN, which is not green")

        # dump ledger: unprotected state is a breach, not an absence
        dumps = self.state.read_dumps()
        last_dump = dumps[-1] if dumps else None
        dump_age = None
        if live is not None:
            if last_dump is None:
                breaches.append(
                    "no dump has EVER protected this cluster while it "
                    "holds live state — state that changed since never "
                    "is unprotected state")
            else:
                dump_age = (now - datetime.fromisoformat(last_dump["ts"])
                            ).total_seconds() / 86400
                if dump_age > walls["dump_interval_days"]:
                    breaches.append(
                        f"dump overdue: last dump {last_dump['ts']} "
                        f"({dump_age:.1f}d ago) against "
                        f"dump_interval_days="
                        f"{walls['dump_interval_days']} — everything "
                        f"since is unprotected")

        # rehearsal ledger: the only source of 'restorable'
        rehearsals = self.state.read_rehearsals()
        last_r = rehearsals[-1] if rehearsals else None
        horizon_days = (walls["rehearsal_interval_days"]
                        * walls["rehearsal_horizon_factor"])
        r_age = None
        if last_r is None:
            if dumps:
                breaches.append(
                    "no rehearsal has EVER proven a dump restorable — "
                    "restorable unproven is restorable assumed, and "
                    "that word has exactly one source (the rehearsal "
                    "ledger)")
        else:
            r_age = (now - datetime.fromisoformat(last_r["ts"])
                     ).total_seconds() / 86400
            if last_r["outcome"] == "red":
                breaches.append(
                    f"last rehearsal ({last_r['ts']}) was RED: "
                    f"{last_r.get('mismatches', [])[:3]} — the backup "
                    f"is not restorable as recorded")
            elif last_r["outcome"] == "parked":
                breaches.append(
                    f"last rehearsal ({last_r['ts']}) PARKED: "
                    f"{last_r.get('reason', '?')} — parked is a "
                    f"breach, not a skip; an unparked green claim "
                    f"would be the green-wash")
            elif last_r["outcome"] == "unreachable":
                breaches.append(
                    f"last rehearsal ({last_r['ts']}) could not reach "
                    f"the cluster: {last_r.get('why', '?')} — not a "
                    f"green, and never reported as one")
            if live is not None and r_age > horizon_days:
                breaches.append(
                    f"rehearsal overdue: last ran {last_r['ts']} "
                    f"({r_age:.1f}d ago), horizon is {horizon_days}d "
                    f"(interval {walls['rehearsal_interval_days']}d x "
                    f"factor {walls['rehearsal_horizon_factor']}) — a "
                    f"schedule that quietly stopped is a breach, not "
                    f"an absence")

        provider_backup = None
        if cluster_view is not None:
            provider_backup = {"claim": cluster_view.get("latest_backup"),
                               "restorable_by_customer": False,
                               "note": BACKUP_CLAIM_NOTE}

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "cluster": cluster_view,
            "cluster_id": live["cluster_id"] if live else None,
            "walls": walls,
            "migration_heads": migration_heads,
            "dump": {"last": ({"ts": last_dump["ts"],
                               "key": last_dump["key"],
                               "sha256": last_dump["sha256"]}
                              if last_dump else None),
                     "age_days": round(dump_age, 2)
                     if dump_age is not None else None,
                     "total": len(dumps)},
            "rehearsal": {"last": last_r,
                          "age_days": round(r_age, 2)
                          if r_age is not None else None,
                          "horizon_days": horizon_days,
                          "total_runs": len(rehearsals)},
            "provider_backup": provider_backup,
        }

    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            return {"configured": False}
        out = self.report()
        out.update({"configured": True,
                    "walls_ratified": out.get("walls_ratified", True),
                    "plan": config.get("plan"),
                    "databases": config.get("databases")})
        return out

    def full_report(self) -> dict:
        """kp_report: status plus the ledger tails quoted verbatim."""
        out = self.status()
        out["ledger_tails"] = {
            "migrations": self.state.read_migrations()[-3:],
            "dumps": [{k: v for k, v in d.items()
                       if k not in ("row_counts", "table_digests")}
                      for d in self.state.read_dumps()[-3:]],
            "rehearsals": self.state.read_rehearsals()[-3:],
        }
        return out

    # -- admin (human-approved) ----------------------------------------------
    def configure(self, monthly_spend_cap_usd: int, dump_interval_days: int,
                  rehearsal_interval_days: int, rehearsal_horizon_factor: int,
                  scratch_headroom_factor: int, max_clusters: int,
                  migrations_dir: str | None = None,
                  databases: list[str] | None = None,
                  plan_rate_usd: float = 15.0) -> dict:
        approvals.consume(self.state, "configure")
        for name, v in (("monthly_spend_cap_usd", monthly_spend_cap_usd),
                        ("dump_interval_days", dump_interval_days),
                        ("rehearsal_interval_days", rehearsal_interval_days),
                        ("rehearsal_horizon_factor",
                         rehearsal_horizon_factor),
                        ("scratch_headroom_factor", scratch_headroom_factor),
                        ("max_clusters", max_clusters)):
            if int(v) < 1:
                raise ValueError(f"{name} must be >= 1")
        self.state.init()
        try:
            config = self.state.load_config()   # keep rail facts if present
        except Exception:
            config = {}
        config.update({
            "monthly_spend_cap_usd": int(monthly_spend_cap_usd),
            "dump_interval_days": int(dump_interval_days),
            "rehearsal_interval_days": int(rehearsal_interval_days),
            "rehearsal_horizon_factor": int(rehearsal_horizon_factor),
            "scratch_headroom_factor": int(scratch_headroom_factor),
            "max_clusters": int(max_clusters),
            "plan_rate_usd": float(plan_rate_usd),
        })
        if migrations_dir is not None:
            config["migrations_dir"] = str(migrations_dir)
        if databases is not None:
            config["databases"] = list(databases)
        self.state.save_config(config)
        return {"configured": True, **{w: config[w] for w in WALLS}}

    # -- teardown: final dump first, then gone AND billing stopped ------------
    def teardown(self) -> dict:
        approvals.consume(self.state, "teardown")
        config = self.state.load_config()
        live = self._live_cluster()
        if live is None:
            raise NotProvisioned("nothing to tear down")
        dumps = self.state.read_dumps()
        rehearsals = self.state.read_rehearsals()
        blast = {
            "cluster_id": live["cluster_id"],
            "databases": config.get("databases"),
            "last_dump": ({"ts": dumps[-1]["ts"], "key": dumps[-1]["key"]}
                          if dumps else None),
            "row_counts": dumps[-1]["row_counts"] if dumps else None,
            "last_rehearsal": ({"ts": rehearsals[-1]["ts"],
                                "outcome": rehearsals[-1]["outcome"]}
                               if rehearsals else None)}
        # the FINAL dump is the last state the estate will ever have: a
        # teardown that cannot take and confirm it PARKS
        try:
            final = self.dump()
        except Exception as e:
            raise LimitRefused(
                f"teardown PARKS: the final dump could not be taken and "
                f"confirmed ({type(e).__name__}: {str(e)[:200]}) — "
                f"destroying the only copy is not an exit; blast radius "
                f"was: {blast}") from e

        rail = self._need_rail()
        verdict = rail.delete_cluster(live["cluster_id"])
        undead: list[str] = []
        if verdict != "gone":
            undead.append(f"rail answered '{verdict}' to the delete")
        if rail.get_cluster(live["cluster_id"]) is not None:
            undead.append("GET still answers for the cluster")
        try:
            still = [c for c in rail.list_clusters()
                     if c.get("cluster_id") == live["cluster_id"]]
        except ClusterUnreachable:
            still = []
            undead.append("account inventory unreachable — billing "
                          "stop is UNVERIFIED")
        if still:
            undead.append("the account inventory still lists the "
                          "cluster; charges may still accrue")
        if undead:
            raise IntegrityError(
                f"UNDEAD cluster — teardown NOT done: "
                f"{'; '.join(undead)}. Billing may still be accruing; "
                f"escalate to the owner with this evidence")
        self.state.append_cluster({
            "ts": self._now().isoformat(), "event": "teardown",
            "cluster_id": live["cluster_id"], "blast": blast})
        return {"torn_down": live["cluster_id"], "gone_verified": True,
                "billing_stopped_verified": True, "blast": blast,
                "final_dump": final["dumped"]}
