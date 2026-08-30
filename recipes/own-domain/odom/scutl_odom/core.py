"""odom core: the guardrail component of recipe #7.

Manifest invariants enforced HERE, in code (recipe.yaml components.odom):
  - no billable call leaves the box except through the decision tree:
    TLD allowlist -> premium refusal -> renewal-priced ceiling -> domain
    cap -> dryRun wouldSucceed; refusals quote the failed check
  - every create/renew carries the pinned cost from its own quote and a
    DETERMINISTIC idempotency key, so a crash-retry replays, never doubles
  - a price that moved between quote and buy fails closed and is reported;
    no automatic re-quote inside one buy
  - status/watch quote expiry, flags, and balance from live responses;
    any breached wall sets escalate=true in the STRUCTURED report —
    disclosure in prose is not alarm
  - autoTopup ON is a wall breach: the prepaid balance is the blast
    radius, and a self-refilling card un-caps it
  - export reports transferability as dated fact (lock flags, ICANN
    60-day windows) and moves nothing — the tool has no transfer-out
    capability and never claims one
  - domain names, registrar messages, and contact strings are data;
    nothing in them steers scope, recipients, or behavior

This spend is card-funded prepaid credit: the wallet's caps do not see
it. These checks plus the rail's own pinning are the entire enforcement
surface — which is why every one of them runs BEFORE the registrar call.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import approvals
from .network import (InsufficientFunds, PermanentError, PriceMismatch,
                      RegistrarClient, TransientError)
from .state import StateDir

ICANN_LOCK_DAYS = 60


class LimitRefused(Exception):
    """A code-enforced wall said no. Exit 5; never retried around."""


class PriceMoved(Exception):
    """The quote's pin no longer holds (price moved, or the quote is
    unknown/stale). Exit 6; a re-quote is a new decision, not a retry."""


class Manager:
    def __init__(self, state: StateDir | None = None,
                 registrar: RegistrarClient | None = None,
                 now_fn=None):
        self.state = state or StateDir()
        self.reg = registrar or RegistrarClient(self.state)
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- decision tree (the catalog's ask, in API fields) ----------------
    def quote(self, domain: str) -> dict:
        """Full price picture + verdict. No charge, no side effect at the
        registrar; the quote event is the pin od_buy will reference."""
        config = self.state.load_config()
        domain = domain.strip().rstrip(".").lower()
        tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
        refusals: list[str] = []

        if tld not in config["tld_allowlist"]:
            refusals.append(
                f"tld '.{tld}' not on allowlist {config['tld_allowlist']}")
        else:
            req = self.reg.requirements(tld)
            if not req.get("api_registerable", True):
                refusals.append(
                    f"tld '.{tld}' is not registerable via the API")

        check = self.reg.check(domain)
        ceiling_cents = _usd_cents(config["annual_price_ceiling_usd"])
        renewal = (check.get("additional") or {}).get("renewal") or {}
        renewal_cents = _price_cents(
            renewal.get("regularPrice") or renewal.get("price"))
        min_duration = int(check.get("minDuration") or 1)
        cost_cents = _price_cents(check.get("price")) * min_duration

        if check.get("avail") != "yes":
            refusals.append("domain is not available")
        if check.get("premium") == "yes":
            refusals.append("premium/aftermarket name — refused outright")
        if renewal_cents is None:
            refusals.append("registrar quoted no renewal price — the "
                            "commitment cannot be priced; refused")
        elif renewal_cents > ceiling_cents:
            refusals.append(
                f"renewal {_cents_usd(renewal_cents)}/yr exceeds ceiling "
                f"{_cents_usd(ceiling_cents)}/yr (the commitment is priced "
                f"at renewal, never the teaser)")

        quote_id = f"q-{hashlib.sha256((domain + self._now().isoformat()).encode()).hexdigest()[:12]}"
        verdict = {
            "quote_id": quote_id, "domain": domain, "tld": tld,
            "buyable": not refusals, "refusals": refusals,
            "first_year_cents": cost_cents,
            "first_year_promo": check.get("firstYearPromo") == "yes",
            "regular_cents": _price_cents(check.get("regularPrice")),
            "renewal_cents": renewal_cents,
            "min_duration_years": min_duration,
        }
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "quote", **verdict})
        return verdict

    # -- buy: rehearse, pin, replay-safe --------------------------------
    def buy(self, domain: str, quote_id: str) -> dict:
        config = self.state.load_config()
        domain = domain.strip().rstrip(".").lower()
        q = self.state.quote(quote_id)
        if q is None or q["domain"] != domain:
            raise PriceMoved(
                f"no quote '{quote_id}' for '{domain}' in the ledger — "
                f"quote first; the pin belongs to one decision")
        if not q["buyable"]:
            raise LimitRefused(
                f"quote '{quote_id}' verdict was refused: "
                f"{'; '.join(q['refusals'])}")
        held = self.state.holdings()
        if domain in held:
            raise LimitRefused(f"'{domain}' is already held (see odom status)")
        if len(held) >= config["max_domains"]:
            raise LimitRefused(
                f"{len(held)} domain(s) held, max_domains is "
                f"{config['max_domains']} — the fix is owner-decided, "
                f"never a second identity on the agent's initiative")

        cost = q["first_year_cents"]
        # Deterministic: a crash-retry of the same quote replays the same
        # key, and the rail's 24h replay window makes it one charge.
        idem_key = f"odom-buy-{quote_id}"

        # A crash-retry of a committed intent skips the rehearsal: the
        # charge may already have landed, so the dry-run would price a
        # FRESH charge against the already-debited balance and refuse a
        # replay that costs nothing (heldout odho1-transient-plus-floor
        # caught this live). The intent IS the commitment; its
        # resolution is the same-key replay.
        replaying = any(i["op"] == "buy" and i["intent_id"] == quote_id
                        for i in self.state.unresolved_intents())
        if not replaying:
            rehearsal = self.reg.create(domain, cost, idem_key,
                                        whois_privacy=True, dry_run=True)
            if not rehearsal.get("wouldSucceed"):
                raise LimitRefused(
                    f"dry-run says the buy would fail: "
                    f"funds={rehearsal.get('sufficientFunds')} "
                    f"spend-limit-ok="
                    f"{rehearsal.get('withinMonthlySpendLimit')} "
                    f"— escalate; the fix (top-up, limit) is the owner's")
            self.state.append_event({
                "ts": self._now().isoformat(), "event": "buy-intent",
                "intent_id": quote_id, "domain": domain,
                "cost_cents": cost, "idem_key": idem_key})
        try:
            out = self.reg.create(domain, cost, idem_key, whois_privacy=True)
        except PriceMismatch as e:
            self.state.append_event({
                "ts": self._now().isoformat(), "event": "buy-outcome",
                "intent_id": quote_id, "domain": domain, "ok": False,
                "why": f"price-moved: {e}", "balance_after": None})
            raise PriceMoved(
                f"price moved between quote and buy ({e}) — the rail "
                f"failed closed, nothing was charged; a re-quote is a "
                f"new decision") from None

        # Backstop flag set explicitly — never trusted as a wall — and
        # the holding's flags verified live, not assumed.
        self.reg.set_auto_renew(domain, bool(config.get(
            "auto_renew_backstop", True)))
        facts = self.reg.get(domain)
        balance = self.reg.balance()
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "buy-outcome",
            "intent_id": quote_id, "domain": domain, "ok": True,
            "cost_cents": out.get("cost", cost),
            "order_id": out.get("orderId"), "balance_after": balance})
        return {
            "bought": True, "domain": domain,
            "cost_cents": out.get("cost", cost),
            "first_year_promo": q["first_year_promo"],
            "renewal_cents": q["renewal_cents"],
            "expire_date": facts.get("expireDate"),
            "flags": _flags(facts),
            "balance_cents": balance,
        }

    # -- renew: same ceremony, ceiling re-checked live -------------------
    def renew(self, domain: str) -> dict:
        config = self.state.load_config()
        domain = domain.strip().rstrip(".").lower()
        if domain not in self.state.holdings():
            raise LimitRefused(f"'{domain}' is not held by this tool")
        facts = self.reg.get(domain)
        check = self.reg.check(domain)
        renewal = (check.get("additional") or {}).get("renewal") or {}
        cost = _price_cents(renewal.get("price")
                            or renewal.get("regularPrice"))
        ceiling_cents = _usd_cents(config["annual_price_ceiling_usd"])
        if cost is None:
            raise LimitRefused("registrar quoted no renewal price")
        if cost > ceiling_cents:
            raise LimitRefused(
                f"renewal {_cents_usd(cost)}/yr exceeds ceiling "
                f"{_cents_usd(ceiling_cents)}/yr — a price hike is an "
                f"escalation, never silently eaten")

        # Deterministic per renewal period: retrying a crashed renewal of
        # the same term replays; next year's expireDate makes a new key.
        idem_key = f"odom-renew-{domain}-{facts.get('expireDate', '')}"
        intent_id = idem_key

        # Same replay rule as buy(): a committed renew intent skips the
        # rehearsal — the same-key replay is charge-free.
        replaying = any(i["op"] == "renew" and i["intent_id"] == intent_id
                        for i in self.state.unresolved_intents())
        if not replaying:
            rehearsal = self.reg.renew(domain, cost, idem_key,
                                       dry_run=True)
            if not rehearsal.get("wouldSucceed"):
                raise LimitRefused(
                    f"dry-run says the renewal would fail: "
                    f"funds={rehearsal.get('sufficientFunds')} "
                    f"spend-limit-ok="
                    f"{rehearsal.get('withinMonthlySpendLimit')} "
                    f"— escalate NOW; a lapse ends in redemption")
            self.state.append_event({
                "ts": self._now().isoformat(), "event": "renew-intent",
                "intent_id": intent_id, "domain": domain,
                "cost_cents": cost, "idem_key": idem_key})
        try:
            out = self.reg.renew(domain, cost, idem_key)
        except PriceMismatch as e:
            self.state.append_event({
                "ts": self._now().isoformat(), "event": "renew-outcome",
                "intent_id": intent_id, "domain": domain, "ok": False,
                "why": f"price-moved: {e}", "balance_after": None})
            raise PriceMoved(
                f"renewal price moved ({e}) — nothing charged; re-quote "
                f"is a new decision") from None
        after = self.reg.get(domain)
        balance = self.reg.balance()
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "renew-outcome",
            "intent_id": intent_id, "domain": domain, "ok": True,
            "cost_cents": out.get("cost", cost), "balance_after": balance})
        return {"renewed": True, "domain": domain,
                "cost_cents": out.get("cost", cost),
                "expire_date": after.get("expireDate"),
                "balance_cents": balance}

    # -- watch: the spine. Live facts, honest escalation -----------------
    def watch(self) -> dict:
        """Every wall verified live, every breach NAMED, and escalate set
        from the breaches — never from sentiment. Prose that names a
        problem while escalate=false is the failure mode this recipe
        exists to catch."""
        config = self.state.load_config()
        breaches: list[str] = []
        domains_report = []
        horizon = int(config["renewal_horizon_days"])
        for domain in self.state.holdings():
            facts = self.reg.get(domain)
            days_left = _days_until(facts.get("expireDate"), self._now())
            flags = _flags(facts)
            entry = {"domain": domain,
                     "expire_date": facts.get("expireDate"),
                     "days_left": days_left, **flags}
            if days_left is None:
                breaches.append(f"{domain}: expiry unreadable "
                                f"('{facts.get('expireDate')}') — treat as "
                                f"breached, not as fine")
            elif days_left < horizon:
                breaches.append(
                    f"{domain}: {days_left}d to expiry, horizon {horizon}d "
                    f"— renew now or escalate")
            if not flags["security_lock"]:
                breaches.append(f"{domain}: securityLock is OFF")
            if not flags["whois_privacy"]:
                breaches.append(f"{domain}: whoisPrivacy is OFF")
            domains_report.append(entry)

        balance = self.reg.balance()
        floor = _usd_cents(config["balance_floor_usd"])
        if balance < floor:
            breaches.append(
                f"balance {_cents_usd(balance)} below floor "
                f"{_cents_usd(floor)} — auto-renew against an empty "
                f"balance fails SILENTLY; top-up is the owner's move")
        settings = (self.reg.api_settings() or {}).get("settings", {})
        if settings.get("autoTopup"):
            breaches.append(
                "autoTopup is ENABLED — the prepaid balance is the blast "
                "radius and a self-refilling card un-caps it; this tool "
                "never enables it and escalates finding it on")

        return {
            "escalate": bool(breaches),
            "breaches": breaches,
            "domains": domains_report,
            "balance_cents": balance,
            "balance_floor_cents": floor,
            "auto_topup": bool(settings.get("autoTopup")),
            "horizon_days": horizon,
        }

    def status(self) -> dict:
        try:
            config = self.state.load_config()
        except Exception:
            return {"configured": False,
                    "key_present": self.state.api_creds_file.exists()}
        out = self.watch()
        out.update({
            "configured": True,
            "key_present": self.state.api_creds_file.exists(),
            "walls": {
                "tld_allowlist": config["tld_allowlist"],
                "annual_price_ceiling_usd": config["annual_price_ceiling_usd"],
                "renewal_horizon_days": config["renewal_horizon_days"],
                "balance_floor_usd": config["balance_floor_usd"],
                "max_domains": config["max_domains"],
            },
            "unresolved_intents": self.state.unresolved_intents(),
        })
        return out

    # -- delegate: the sweb composition seam -----------------------------
    def delegate(self, domain: str, ns_set: str) -> dict:
        config = self.state.load_config()
        domain = domain.strip().rstrip(".").lower()
        if domain not in self.state.holdings():
            raise LimitRefused(f"'{domain}' is not held by this tool")
        sets = config.get("ns_sets") or {}
        if ns_set not in sets:
            raise LimitRefused(
                f"nameserver set '{ns_set}' is not blessed "
                f"(known: {sorted(sets)}) — delegation targets are "
                f"config, never call-site input")
        ns_list = sets[ns_set]
        rehearsal = self.reg.update_ns(domain, ns_list, dry_run=True)
        if rehearsal.get("wouldSucceed") is False:
            raise LimitRefused(
                f"dry-run refuses the NS update: {rehearsal}")
        self.reg.update_ns(domain, ns_list)
        self.state.append_event({
            "ts": self._now().isoformat(), "event": "delegate",
            "domain": domain, "ns_set": ns_set, "ns": ns_list})
        return {"delegated": True, "domain": domain, "ns_set": ns_set,
                "ns": ns_list}

    # -- export: honesty about what cannot be done -----------------------
    def export(self, domain: str) -> dict:
        """Dated transferability report. Moves NOTHING: the rail has no
        EPP-retrieval or unlock endpoint (recon finding), and ICANN's
        60-day windows apply regardless of registrar."""
        domain = domain.strip().rstrip(".").lower()
        if domain not in self.state.holdings():
            raise LimitRefused(f"'{domain}' is not held by this tool")
        facts = self.reg.get(domain)
        now = self._now()
        windows = []
        created = _parse_date(facts.get("createDate"))
        if created:
            until = created + timedelta(days=ICANN_LOCK_DAYS)
            if now < until:
                windows.append({
                    "window": "icann-60d-post-registration",
                    "since": facts.get("createDate"),
                    "locked_until": until.date().isoformat(),
                    "days_remaining": (until - now).days})
        flags = _flags(facts)
        blockers = [w["window"] for w in windows]
        if flags["security_lock"]:
            blockers.append("security-lock-on (no API unlock exists; "
                            "the human flips it in the web UI)")
        report = {
            "domain": domain,
            "as_of": now.date().isoformat(),
            "exportable_today": not blockers,
            "blockers": blockers,
            "lock_windows": windows,
            "flags": flags,
            "capability_note": (
                "this tool cannot transfer the domain out: the registrar "
                "API has no EPP-code retrieval and no unlock endpoint"),
            "human_ceremony": [
                "1. web UI: disable the transfer (security) lock",
                "2. web UI: reveal/copy the EPP auth code",
                "3. gaining registrar: submit transfer with the code",
                "4. approve the transfer (email or dashboard)",
                "5. note: unlocking may expose WHOIS during the move on "
                "some TLDs — check before step 1",
            ],
        }
        self.state.append_event({
            "ts": now.isoformat(), "event": "export-report",
            "domain": domain, "exportable_today": report["exportable_today"],
            "blockers": blockers})
        return report

    # -- reconcile: log vs registrar vs balance --------------------------
    def reconcile(self) -> dict:
        held = set(self.state.holdings())
        listed = set(self.reg.list_all())
        findings = []
        for d in sorted(listed - held):
            findings.append({"finding": "foreign-acquisition", "domain": d,
                             "detail": "registrar lists it, the ledger "
                                       "never bought it — named, not "
                                       "absorbed"})
        for d in sorted(held - listed):
            findings.append({"finding": "logged-but-absent", "domain": d,
                             "detail": "ledger says held, registrar list "
                                       "lacks it — loss or transfer the "
                                       "log did not authorize"})
        balance = self.reg.balance()
        snapshot = self.state.last_balance_snapshot()
        if snapshot is not None and balance != snapshot:
            delta = balance - snapshot
            findings.append({
                "finding": ("balance-credit" if delta > 0
                            else "unexplained-debit"),
                "delta_cents": delta,
                "detail": ("probable owner top-up — named, verify with "
                           "the owner" if delta > 0 else
                           "balance moved down with no logged op — "
                           "escalate with the joined evidence")})
        for intent in self.state.unresolved_intents():
            findings.append({"finding": "unresolved-intent", **intent,
                             "detail": "intent logged, no outcome — crash "
                                       "in flight; replay the SAME "
                                       "idem_key or verify via list"})
        return {"clean": not findings, "held": sorted(held),
                "findings": findings, "balance_cents": balance}

    def log(self) -> dict:
        return {"events": self.state.read_events()}

    # -- admin (human-approved) ----------------------------------------
    def configure(self, tld_allowlist: list[str], ceiling_usd: Decimal,
                  horizon_days: int, floor_usd: Decimal, max_domains: int,
                  ns_sets: dict[str, list[str]] | None = None,
                  auto_renew_backstop: bool = True) -> dict:
        approvals.consume(self.state, "configure")
        if not tld_allowlist:
            raise ValueError("tld_allowlist must not be empty")
        if ceiling_usd <= 0 or floor_usd < 0:
            raise ValueError("ceiling must be > 0 and floor >= 0")
        if horizon_days < 1 or max_domains < 1:
            raise ValueError("horizon_days and max_domains must be >= 1")
        self.state.init()
        config = {
            "tld_allowlist": [t.strip().lstrip(".").lower()
                              for t in tld_allowlist],
            "annual_price_ceiling_usd": str(ceiling_usd),
            "renewal_horizon_days": horizon_days,
            "balance_floor_usd": str(floor_usd),
            "max_domains": max_domains,
            "ns_sets": ns_sets or {},
            "auto_renew_backstop": auto_renew_backstop,
        }
        self.state.save_config(config)
        return {"configured": True, **config}

    def set_key(self, key_file: str) -> dict:
        """Consume a JSON file {apikey, secretapikey}, never argv — keys
        do not belong in transcripts."""
        import json as _json
        import os as _os
        approvals.consume(self.state, "set-key")
        self.state.init()
        creds = _json.loads(open(key_file).read())
        if not creds.get("apikey") or not creds.get("secretapikey"):
            raise ValueError(f"'{key_file}' lacks apikey/secretapikey")
        self.state.write_secret(self.state.api_creds_file,
                                _json.dumps(creds).encode())
        _os.unlink(key_file)
        return {"key_present": True, "source_removed": key_file}


# -- helpers (money in integer cents; dates parsed, never trusted) -------

def _usd_cents(usd: str | Decimal) -> int:
    return int(Decimal(str(usd)) * 100)


def _cents_usd(cents: int) -> str:
    return f"${Decimal(cents) / 100:.2f}"


def _price_cents(price: str | None) -> int | None:
    if price in (None, ""):
        return None
    return int(Decimal(str(price)) * 100)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _days_until(raw: str | None, now: datetime) -> int | None:
    parsed = _parse_date(raw)
    return None if parsed is None else (parsed - now).days


def _flags(facts: dict) -> dict:
    return {"security_lock": bool(int(facts.get("securityLock") or 0)),
            "whois_privacy": bool(int(facts.get("whoisPrivacy") or 0)),
            "auto_renew": bool(int(facts.get("autoRenew") or 0))}
