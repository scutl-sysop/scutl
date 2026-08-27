"""rev 3 (cst-rjba, recipe x402v2): --probe, --binding, and bazaar
request lowering. Same mock discipline as test_rev2.py; the live probe
against the reference merchant runs in acceptance."""

import json

import pytest

from scutl_signer import buy
from scutl_signer.bazaar import (
    BazaarError,
    extract_input,
    lower_request,
)
from scutl_signer.network import BLESSED

MAINNET = BLESSED["eip155:8453"]

BAZAAR_INPUT = {
    "type": "http", "method": "POST", "bodyType": "json",
    "body": {"domain": "<domain>", "username": "<username>",
             "client_id": "<client_id>", "display_name": "<display_name>"},
}


def _v2_quote(bazaar=True, proxy=True):
    """AgentMail-shaped: proxy-fronted, multi-chain, bazaar schema."""
    q = {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": "https://api.paysponge.test/v0/inboxes",
                     "description": "inbox", "mimeType": "application/json"},
        "accepts": [
            {"scheme": "exact", "network": "eip155:8453",
             "amount": "2000000", "asset": MAINNET.usdc_address,
             "payTo": "0x" + "6e" * 20, "maxTimeoutSeconds": 300,
             "extra": {"name": "USD Coin", "version": "2"}},
            {"scheme": "exact", "network": "eip155:137",
             "amount": "2000000", "asset": "0x" + "3c" * 20,
             "payTo": "0x" + "6e" * 20, "maxTimeoutSeconds": 300,
             "extra": {"name": "USD Coin", "version": "2"}},
        ],
        "extensions": ({"bazaar": {"info": {"input": BAZAAR_INPUT}}}
                       if bazaar else {}),
    }
    if not proxy:
        q["resource"]["url"] = "https://x402.merchant.test/v0/inboxes"
    return q


class FakeResponse:
    def __init__(self, status_code=402, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


# -- bazaar lowering ---------------------------------------------------

def test_lower_request_fills_only_caller_fields():
    lowered = lower_request(BAZAAR_INPUT, {"username": "star"})
    assert lowered.method == "POST"
    assert json.loads(lowered.body) == {"username": "star"}
    # schema placeholders ("<domain>") never leak into the body


def test_lower_request_rejects_undeclared_field():
    with pytest.raises(BazaarError, match="not declared"):
        lower_request(BAZAAR_INPUT, {"usernme": "star"})  # typo'd


def test_lower_request_rejects_non_json_body_type():
    bad = dict(BAZAAR_INPUT, bodyType="form-data")
    with pytest.raises(BazaarError, match="unsupported"):
        lower_request(bad, {})


def test_lower_request_rejects_unlisted_method():
    bad = dict(BAZAAR_INPUT, method="DELETE")
    with pytest.raises(BazaarError, match="method"):
        lower_request(bad, {})


def test_lower_request_rejects_non_http_type():
    bad = dict(BAZAAR_INPUT, type="grpc")
    with pytest.raises(BazaarError, match="not 'http'"):
        lower_request(bad, {})


def test_extract_input_absent_for_plain_offers():
    assert extract_input({}) is None
    assert extract_input({"bazaar": {}}) is None
    assert extract_input(_v2_quote()["extensions"]) == BAZAAR_INPUT


# -- probe -------------------------------------------------------------

def test_probe_reports_offer_without_paying(monkeypatch, tmp_path):
    calls = []

    def fake_request(method, url, body, headers=None, timeout=30):
        calls.append({"method": method, "headers": dict(headers or {})})
        return FakeResponse(body=_v2_quote())

    monkeypatch.setattr(buy, "_request", fake_request)
    monkeypatch.setattr(buy, "_ambient_binding", lambda: MAINNET)
    report = buy._probe("https://x402.merchant.test/v0/inboxes", "POST", "{}")
    assert report["probe"] is True
    assert report["x402_version"] == 2
    assert report["selected"]["network"] == "eip155:8453"
    assert report["selected"]["amount_usdc"] == "2.000000"
    assert report["proxy"] is True
    assert report["resource_host"] == "api.paysponge.test"
    assert report["bazaar"]["method"] == "POST"
    assert sorted(report["bazaar"]["fields"]) == [
        "client_id", "display_name", "domain", "username"]
    # exactly one request, and it carried no payment header
    assert len(calls) == 1
    assert not any("PAYMENT" in k.upper() for k in calls[0]["headers"])


def test_probe_reports_refusal_when_no_blessed_offer(monkeypatch):
    quote = _v2_quote()
    quote["accepts"] = [r for r in quote["accepts"]
                        if r["network"] != "eip155:8453"]
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(body=quote))
    monkeypatch.setattr(buy, "_ambient_binding", lambda: MAINNET)
    report = buy._probe("https://x402.merchant.test/x", "GET", None)
    assert report["selected"] is None
    assert "eip155:8453" in report["refusal"]


def test_probe_non_402_is_permanent(monkeypatch):
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(status_code=200))
    monkeypatch.setattr(buy, "_ambient_binding", lambda: MAINNET)
    with pytest.raises(buy.PermanentError, match="expected 402"):
        buy._probe("https://x402.merchant.test/x", "GET", None)


def test_probe_no_proxy_when_hosts_match(monkeypatch):
    monkeypatch.setattr(
        buy, "_request",
        lambda *a, **k: FakeResponse(body=_v2_quote(proxy=False)))
    monkeypatch.setattr(buy, "_ambient_binding", lambda: MAINNET)
    report = buy._probe("https://x402.merchant.test/v0/inboxes", "GET", None)
    assert report["proxy"] is False


# -- binding report ----------------------------------------------------

def test_binding_report_defaults_to_sepolia_without_state(monkeypatch):
    monkeypatch.setenv("SCUTL_STATE", "/nonexistent/scutl-test-void")
    report = buy._binding_report()
    assert report["binding"] == "eip155:84532"
    assert report["testnet"] is True
    assert report["eip712_name"] == "USDC"


# -- CLI surface -------------------------------------------------------

def test_cli_binding_flag_needs_no_url(monkeypatch, capsys):
    monkeypatch.setenv("SCUTL_STATE", "/nonexistent/scutl-test-void")
    buy.main(["--binding"])
    out = json.loads(capsys.readouterr().out)
    assert out["binding"] == "eip155:84532"


def test_cli_url_required_without_binding(capsys):
    with pytest.raises(SystemExit) as e:
        buy.main([])
    assert e.value.code == 7


def test_cli_field_and_data_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as e:
        buy.main(["https://x", "--payment-id", "p1",
                  "--field", "a=b", "--data", "{}"])
    assert e.value.code == 7


def test_cli_field_must_be_name_value(capsys):
    with pytest.raises(SystemExit) as e:
        buy.main(["https://x", "--payment-id", "p1", "--field", "nope"])
    assert e.value.code == 7


def test_cli_probe_prints_report(monkeypatch, capsys):
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(body=_v2_quote()))
    monkeypatch.setattr(buy, "_ambient_binding", lambda: MAINNET)
    buy.main(["https://x402.merchant.test/v0/inboxes", "--probe"])
    out = json.loads(capsys.readouterr().out)
    assert out["probe"] is True and out["selected"] is not None


def test_cli_buy_requires_payment_id(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        buy.main(["https://x"])
    assert e.value.code == 7


# -- buy with bazaar fields -------------------------------------------

def test_buy_retry_carries_lowered_body(monkeypatch):
    """Zero-amount flow end-to-end: the paid retry's body is the bazaar
    lowering of the caller's fields, not the '{}' placeholder."""
    quote = _v2_quote()
    for r in quote["accepts"]:
        r["amount"] = "0"
    sent = []
    responses = [FakeResponse(body=quote), FakeResponse(status_code=200)]

    def fake_request(m, url, body, headers=None, timeout=30):
        sent.append({"method": m, "body": body,
                     "headers": dict(headers or {})})
        return responses.pop(0)

    class FakeSigner:
        binding = MAINNET

        def authorize(self, *a, **k):
            return {"header": "sig"}

        def record_settled(self, *a, **k):
            return {"amount": "0"}

    monkeypatch.setattr(buy, "_request", fake_request)
    monkeypatch.setattr(buy, "Signer", lambda: FakeSigner())
    out = buy._buy("https://x402.merchant.test/v0/inboxes", "p1", None,
                   method="POST", body="{}",
                   fields={"username": "star"})
    assert sent[0]["body"] == "{}"
    assert json.loads(sent[1]["body"]) == {"username": "star"}
    assert "PAYMENT-SIGNATURE" in sent[1]["headers"]
    assert out["quote"]["amount_usdc"] == "0.000000"


def test_buy_field_requires_bazaar_schema(monkeypatch):
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(
                            body=_v2_quote(bazaar=False)))

    class FakeSigner:
        binding = MAINNET

    monkeypatch.setattr(buy, "Signer", lambda: FakeSigner())
    with pytest.raises(buy.PermanentError, match="no bazaar"):
        buy._buy("https://x", "p1", None, method="POST", body="{}",
                 fields={"username": "star"})


def test_buy_field_method_must_match_schema(monkeypatch):
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(body=_v2_quote()))

    class FakeSigner:
        binding = MAINNET

    monkeypatch.setattr(buy, "Signer", lambda: FakeSigner())
    with pytest.raises(buy.PermanentError, match="--method POST"):
        buy._buy("https://x", "p1", None, method="PUT", body="{}",
                 fields={"username": "star"})


def test_buy_field_undeclared_is_permanent(monkeypatch):
    monkeypatch.setattr(buy, "_request",
                        lambda *a, **k: FakeResponse(body=_v2_quote()))

    class FakeSigner:
        binding = MAINNET

    monkeypatch.setattr(buy, "Signer", lambda: FakeSigner())
    with pytest.raises(buy.PermanentError, match="not declared"):
        buy._buy("https://x", "p1", None, method="POST", body="{}",
                 fields={"evil": "x"})
