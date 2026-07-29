"""
tests/test_kalshi_private.py
────────────────────────────
Tests for src/kalshi_private.py — the authenticated Kalshi client.

This module can spend real money, so the properties under test are mostly
*refusals*:

  * **Signing is exactly the documented construction.** ``timestamp_ms +
    METHOD + path``, path including ``/trade-api/v2`` and **excluding** the
    query string, RSA-PSS/MGF1-SHA256 at digest salt length, base64. Verified
    against a real signature check, not a shape assertion — a silently wrong
    signature fails as 401 at the worst moment.
  * **Closed by default.** Orders raise unless explicitly enabled.
  * **The loss guard fails CLOSED.** If settlements cannot be read it halts,
    rather than assuming the budget is intact.
  * **The guard reads the exchange, not memory.** A fresh guard on a restarted
    process must see prior losses. That reset is the P-014 failure mode.
  * **Key material never appears in repr/str.** The .env leak of 2026-07-28 is
    the reason this is a test and not a convention.
  * **Combo prices snap to the 0.1c grid.** Whole-cent rounding sends off-grid
    prices that the exchange rejects.
"""
import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_private import (BASE_PATH, KalshiAuth, KalshiAuthError,
                                KalshiPrivate, LossGuard, LossHalt,
                                OrdersDisabled, TokenBucket, grid_step, to_fixed)

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ── fixtures ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption())
    return key, pem


@pytest.fixture
def auth(keypair):
    _key, pem = keypair
    return KalshiAuth("test-key-id-0123456789", pem)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ── signing ───────────────────────────────────────────────────────────
def test_signature_verifies_against_documented_message(auth, keypair):
    """The signature must verify over exactly timestamp+METHOD+path."""
    key, _ = keypair
    ts = 1753800000000
    headers = auth.sign("GET", "/trade-api/v2/portfolio/balance", timestamp_ms=ts)
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    expected = f"{ts}GET/trade-api/v2/portfolio/balance".encode()

    key.public_key().verify(
        sig, expected,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())          # raises InvalidSignature on mismatch


def test_signature_does_not_cover_a_different_message(auth, keypair):
    from cryptography.exceptions import InvalidSignature
    key, _ = keypair
    ts = 1753800000000
    headers = auth.sign("GET", "/trade-api/v2/portfolio/balance", timestamp_ms=ts)
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    # query string included -> must NOT verify
    wrong = f"{ts}GET/trade-api/v2/portfolio/balance?limit=5".encode()
    with pytest.raises(InvalidSignature):
        key.public_key().verify(
            sig, wrong,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())


def test_query_string_is_excluded_from_the_signed_path(auth, keypair, monkeypatch):
    """A signed path carrying ?params is the classic 401 cause."""
    key, _ = keypair
    seen = {}

    def fake_request(method, url, params=None, data=None, headers=None, timeout=None):
        seen["ts"] = int(headers["KALSHI-ACCESS-TIMESTAMP"])
        seen["sig"] = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
        return FakeResponse(200, {"fills": [], "cursor": None})

    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", fake_request)
    k.get("/portfolio/fills", {"limit": 100, "ticker": "KXTEST"})

    key.public_key().verify(
        seen["sig"], f"{seen['ts']}GET{BASE_PATH}/portfolio/fills".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())


def test_headers_are_the_three_documented_names(auth):
    h = auth.sign("POST", "/trade-api/v2/portfolio/orders")
    assert set(h) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP",
                      "KALSHI-ACCESS-SIGNATURE"}
    assert h["KALSHI-ACCESS-KEY"] == "test-key-id-0123456789"
    assert h["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(h["KALSHI-ACCESS-TIMESTAMP"]) == 13      # milliseconds, not seconds


# ── key material must never leak ──────────────────────────────────────
def test_repr_never_contains_key_material(auth, keypair):
    _key, pem = keypair
    body = pem.decode().splitlines()[1]                 # a real base64 line
    for text in (repr(auth), str(auth), repr(KalshiPrivate(auth))):
        assert body not in text
        assert "PRIVATE KEY" not in text
        assert "<redacted>" in text or "key=" in text


def test_bad_pem_error_does_not_echo_the_key():
    secret = b"-----BEGIN RSA PRIVATE KEY-----\nSUPERSECRETMATERIAL\n-----END RSA PRIVATE KEY-----"
    with pytest.raises(KalshiAuthError) as exc:
        KalshiAuth("kid", secret)
    assert "SUPERSECRETMATERIAL" not in str(exc.value)


def test_from_env_prefers_path_over_inline(tmp_path, keypair):
    _key, pem = keypair
    p = tmp_path / "kalshi.pem"
    p.write_bytes(pem)
    a = KalshiAuth.from_env({"KALSHI_API_KEY_ID": "kid",
                             "KALSHI_PRIVATE_KEY_PATH": str(p),
                             "KALSHI_PRIVATE_KEY": "garbage-would-not-parse"})
    assert a.key_id == "kid"


def test_from_env_missing_key_is_a_clear_error():
    with pytest.raises(KalshiAuthError, match="KALSHI_PRIVATE_KEY_PATH"):
        KalshiAuth.from_env({"KALSHI_API_KEY_ID": "kid"})


def test_from_env_missing_path_file_says_so(tmp_path):
    with pytest.raises(KalshiAuthError, match="does not exist"):
        KalshiAuth.from_env({"KALSHI_API_KEY_ID": "kid",
                             "KALSHI_PRIVATE_KEY_PATH": str(tmp_path / "nope.pem")})


# ── closed by default ─────────────────────────────────────────────────
def test_orders_disabled_by_default(auth):
    k = KalshiPrivate(auth)
    assert k.allow_orders is False
    with pytest.raises(OrdersDisabled):
        k.place_order(ticker="KXTEST", side="yes", yes_price=5, count=1)


def test_rfq_and_quote_also_refuse_when_read_only(auth):
    k = KalshiPrivate(auth)
    for call in (lambda: k.create_rfq("KXTEST", contracts=1),
                 lambda: k.create_quote("rfq1", yes_bid="0.05", no_bid="0.90"),
                 lambda: k.confirm_quote("q1"),
                 lambda: k.delete_quote("q1")):
        with pytest.raises(OrdersDisabled):
            call()


def test_dry_run_never_reaches_the_network(auth, monkeypatch):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)

    def explode(*a, **kw):
        raise AssertionError("dry_run must not send a request")
    monkeypatch.setattr(k._s, "request", explode)

    out = k.place_order(ticker="KXTEST", side="yes", count=2,
                        yes_price_dollars="0.0630", action="sell")
    assert out["dry_run"] is True
    assert out["order"]["yes_price_dollars"] == "0.0630"
    assert out["order"]["action"] == "sell"


# ── order validation ──────────────────────────────────────────────────
@pytest.mark.parametrize("kw,msg", [
    (dict(ticker="T", side="maybe", count=1, yes_price=5), "side must be"),
    (dict(ticker="T", side="yes", count=0, yes_price=5), "count must be"),
    (dict(ticker="T", side="yes", count=1, action="hedge"), "action must be"),
    (dict(ticker="T", side="yes", count=1), "needs a price"),
])
def test_place_order_rejects_bad_arguments(auth, kw, msg):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    with pytest.raises(ValueError, match=msg):
        k.place_order(**kw)


def test_place_order_generates_an_idempotency_key(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    a = k.place_order(ticker="T", side="yes", count=1, yes_price=5)
    b = k.place_order(ticker="T", side="yes", count=1, yes_price=5)
    assert a["order"]["client_order_id"] != b["order"]["client_order_id"]


# ── quote validation mirrors the exchange's own rules ─────────────────
def test_quote_rejects_both_sides_zero(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    with pytest.raises(ValueError, match="non-zero"):
        k.create_quote("rfq1", yes_bid="0", no_bid="0")


def test_quote_rejects_sum_above_one_dollar(auth):
    """The exchange rejects yes_bid + no_bid > $1; catch it locally first."""
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    with pytest.raises(ValueError, match=r"> \$1"):
        k.create_quote("rfq1", yes_bid="0.60", no_bid="0.55")


def test_quote_accepts_a_one_sided_decline(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    out = k.create_quote("rfq1", yes_bid="0", no_bid="0.94")
    assert out["quote"]["no_bid"] == "0.94"


def test_accept_quote_validates_side(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    with pytest.raises(ValueError, match="accepted_side"):
        k.accept_quote("q1", "both")


# ── the 0.1c combo grid ───────────────────────────────────────────────
def test_to_fixed_snaps_to_the_deci_cent_grid():
    assert to_fixed(0.0634, 0.001) == "0.0630"
    assert to_fixed(0.0636, 0.001) == "0.0640"
    assert to_fixed(0.20321, 0.001) == "0.2030"


def test_to_fixed_snaps_to_whole_cents_by_default():
    assert to_fixed(0.0634) == "0.0600"
    assert to_fixed(0.0651) == "0.0700"


def test_to_fixed_rejects_out_of_range():
    for bad in (-0.01, 1.01):
        with pytest.raises(ValueError):
            to_fixed(bad)


def test_grid_step_reads_price_ranges_not_tick_size():
    """GET /markets returns tick_size: None even for sub-cent markets."""
    combo = {"tick_size": None,
             "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0010"}]}
    assert grid_step(combo) == 0.001
    assert grid_step({"tick_size": None}) == 0.01          # safe default


# ── loss guard ────────────────────────────────────────────────────────
class StubClient:
    """Stands in for KalshiPrivate; returns canned settlements."""

    def __init__(self, rows=None, raises=False, subaccount=1):
        self.rows = rows or []
        self.raises = raises
        self.calls = 0
        self.last_query = None
        self.subaccount = subaccount

    def get_settlements(self, **kw):
        self.calls += 1
        self.last_query = kw
        if self.raises:
            raise RuntimeError("network down")
        return self.rows


def _settlement(pnl_dollars, day="2026-07-28"):
    return {"realized_pnl_dollars": f"{pnl_dollars:.4f}",
            "settled_time": f"{day}T12:00:00Z"}


def _real_settlement(revenue_cents, yes_cost=0.0, no_cost=0.0, fee=0.0,
                     day="2026-07-28"):
    """A settlement in the shape Kalshi ACTUALLY returns.

    Verified against a live account 2026-07-29. There is no realized_pnl field
    of any kind; `revenue` and `value` are integer CENTS while the cost fields
    are fixed-point DOLLAR strings.
    """
    return {
        "event_ticker": "KXTEST-26JUL28",
        "ticker": "KXTEST-26JUL28-A",
        "market_result": "yes",
        "yes_count_fp": "10.00",
        "no_count_fp": "0.00",
        "yes_total_cost_dollars": f"{yes_cost:.4f}",
        "no_total_cost_dollars": f"{no_cost:.4f}",
        "fee_cost": f"{fee:.4f}",
        "revenue": int(revenue_cents),
        "value": 100,
        "settled_time": f"{day}T12:00:00Z",
    }


# ── settlement P&L derivation (Kalshi returns no P&L field) ───────────
def test_pnl_derived_from_revenue_and_costs():
    """revenue is CENTS, costs are DOLLARS — the unit trap that made the
    guard blind on a live account."""
    from src.kalshi_private import settlement_pnl
    # $10.00 revenue against $4 + $1 cost and $0.25 fees -> +$4.75
    row = _real_settlement(1000, yes_cost=4.0, no_cost=1.0, fee=0.25)
    assert settlement_pnl(row) == pytest.approx(4.75)


def test_pnl_derivation_reports_a_loss():
    from src.kalshi_private import settlement_pnl
    row = _real_settlement(0, yes_cost=12.50, fee=0.30)
    assert settlement_pnl(row) == pytest.approx(-12.80)


def test_pnl_prefers_documented_field_when_present():
    """Forward-compatible: if Kalshi ever adds realized_pnl_dollars, use it."""
    from src.kalshi_private import settlement_pnl
    row = _real_settlement(1000, yes_cost=4.0)
    row["realized_pnl_dollars"] = "99.0000"
    assert settlement_pnl(row) == pytest.approx(99.0)


def test_pnl_accepts_cent_denominated_realized_pnl():
    from src.kalshi_private import settlement_pnl
    assert settlement_pnl({"realized_pnl": -2550}) == pytest.approx(-25.50)


def test_pnl_is_none_when_nothing_is_derivable():
    from src.kalshi_private import settlement_pnl
    assert settlement_pnl({"ticker": "X", "settled_time": "2026-07-28T00:00:00Z"}) is None


def test_guard_halts_using_the_real_schema():
    """The live-schema regression: this is what was silently returning $0."""
    rows = [_real_settlement(0, yes_cost=200.0) for _ in range(3)]   # -$600
    g = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt, match="permanent halt"):
        g.check()
    assert g.snapshot().total_realised == pytest.approx(600.0)


def test_guard_halts_when_it_cannot_read_ANY_row():
    """Blind must mean halt, never 'no loss'.

    Regression for the 2026-07-29 finding: 830 live settlements, zero readable,
    guard reported $0.00 and would never have stopped a losing run.
    """
    rows = [{"ticker": "X", "settled_time": "2026-07-28T00:00:00Z"}
            for _ in range(830)]
    g = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt, match="blind"):
        g.check()
    s = g.snapshot()
    assert s.rows_seen == 830 and s.rows_readable == 0


def test_guard_does_not_halt_on_an_empty_account():
    """No settlements at all is not the same as unreadable settlements."""
    g = LossGuard(StubClient([]), max_total_loss=500.0, max_daily_loss=100.0)
    g.check()


# ── `since` scoping: prior history must not eat the budget ────────────
def test_since_excludes_settlements_before_the_experiment():
    rows = [_real_settlement(0, yes_cost=900.0, day="2026-06-01"),   # -$900 prior
            _real_settlement(0, yes_cost=10.0, day="2026-08-02")]    # -$10 in scope
    g = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0,
                  since="2026-08-01")
    g.check()                                   # prior loss must not halt us
    s = g.snapshot()
    assert s.total_realised == pytest.approx(10.0)
    assert s.rows_seen == 1


def test_without_since_prior_history_would_halt_immediately():
    """Documents exactly why `since` matters — same rows, no scoping."""
    rows = [_real_settlement(0, yes_cost=900.0, day="2026-06-01")]
    g = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt):
        g.check()


def test_since_accepts_a_full_timestamp():
    rows = [_real_settlement(0, yes_cost=50.0, day="2026-08-05")]
    g = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0,
                  since="2026-08-01T00:00:00Z")
    g.check()
    assert g.snapshot().total_realised == pytest.approx(50.0)


def test_guard_allows_trading_below_the_limits():
    g = LossGuard(StubClient([_settlement(-10.0), _settlement(5.0)]),
                  max_total_loss=500.0, max_daily_loss=100.0)
    g.check()                                      # must not raise
    assert g.snapshot().total_realised == pytest.approx(10.0)


def test_guard_halts_permanently_on_cumulative_loss():
    g = LossGuard(StubClient([_settlement(-501.0)]),
                  max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt, match="permanent halt"):
        g.check()


def test_guard_pauses_on_daily_loss():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    g = LossGuard(StubClient([_settlement(-120.0, today)]),
                  max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt, match="paused"):
        g.check()


def test_guard_ignores_losses_from_other_days_for_the_daily_cap():
    g = LossGuard(StubClient([_settlement(-120.0, "2020-01-01")]),
                  max_total_loss=500.0, max_daily_loss=100.0)
    g.check()                                      # yesterday's loss is not today's
    assert g.snapshot().today_realised == 0.0


def test_guard_fails_closed_when_the_exchange_cannot_be_read():
    """Unreadable settlements must halt, never be treated as zero loss."""
    g = LossGuard(StubClient(raises=True), max_total_loss=500.0,
                  max_daily_loss=100.0)
    with pytest.raises(LossHalt, match="could not read settlements"):
        g.check()


def test_guard_reads_the_exchange_not_an_in_process_counter():
    """A restarted process must still see prior losses (the P-014 lesson)."""
    rows = [_settlement(-501.0)]
    fresh = LossGuard(StubClient(rows), max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt):
        fresh.check()                              # no prior state, still halts


def test_guard_handles_cent_denominated_settlements():
    client = StubClient([{"realized_pnl": -50100,
                          "settled_time": "2026-07-28T12:00:00Z"}])
    g = LossGuard(client, max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt):
        g.check()


def test_guard_reset_clears_a_halt():
    g = LossGuard(StubClient([_settlement(-501.0)]), max_total_loss=500.0,
                  max_daily_loss=100.0)
    with pytest.raises(LossHalt):
        g.check()
    g.client.rows = []
    g.reset()
    g.check()


def test_guard_blocks_orders_through_the_client(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    k.loss_guard = LossGuard(StubClient([_settlement(-501.0)]),
                             max_total_loss=500.0, max_daily_loss=100.0)
    with pytest.raises(LossHalt):
        k.place_order(ticker="T", side="yes", count=1, yes_price=5)


def test_guard_rejects_nonsense_limits():
    for kw in ({"max_total_loss": 0}, {"max_daily_loss": -1}):
        with pytest.raises(ValueError):
            LossGuard(StubClient(), **kw)


# ── transport behaviour ───────────────────────────────────────────────
def test_204_returns_empty_dict_not_none(auth, monkeypatch):
    """Accept/confirm return 204; None would read as failure."""
    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request",
                        lambda *a, **kw: FakeResponse(204, None, ""))
    assert k.request("PUT", "/communications/quotes/q/confirm") == {}


def test_pagination_follows_the_cursor(auth, monkeypatch):
    pages = [
        FakeResponse(200, {"fills": [{"id": 1}], "cursor": "c1"}),
        FakeResponse(200, {"fills": [{"id": 2}], "cursor": None}),
    ]
    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", lambda *a, **kw: pages.pop(0))
    assert [f["id"] for f in k.get_fills()] == [1, 2]


def test_retries_on_429_then_succeeds(auth, monkeypatch):
    seq = [FakeResponse(429, None, "slow down"),
           FakeResponse(200, {"balance_dollars": "123.4500"})]
    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", lambda *a, **kw: seq.pop(0))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    assert k.get_balance() == pytest.approx(123.45)


def test_401_is_not_retried_and_returns_none(auth, monkeypatch):
    calls = {"n": 0}

    def once(*a, **kw):
        calls["n"] += 1
        return FakeResponse(401, None, "unauthorized")

    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", once)
    assert k.get_balance() is None
    assert calls["n"] == 1                     # a bad key must fail fast
    assert ("GET", "/portfolio/balance", 401) in k.status_log


def test_balance_falls_back_to_cent_field(auth, monkeypatch):
    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request",
                        lambda *a, **kw: FakeResponse(200, {"balance": 6789}))
    assert k.get_balance() == pytest.approx(67.89)


def test_quote_calls_use_the_discounted_token_cost(auth, monkeypatch):
    """Quote create/cancel cost 2 tokens, not the default 10."""
    seen = []
    k = KalshiPrivate(auth, allow_orders=True)
    monkeypatch.setattr(k.bucket, "acquire", lambda cost=10: seen.append(cost) or 0.0)
    monkeypatch.setattr(k._s, "request", lambda *a, **kw: FakeResponse(201, {"id": "q"}))
    k.create_quote("rfq", yes_bid="0.05", no_bid="0.90")
    k.place_order(ticker="T", side="yes", count=1, yes_price=5)
    assert seen == [2, 10]


# ── rate limiting ─────────────────────────────────────────────────────
def test_token_bucket_blocks_when_drained(monkeypatch):
    b = TokenBucket(tokens_per_s=100, burst=10)
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    b.acquire(10)
    b.acquire(10)                       # must wait for a refill
    assert slept and sum(slept) > 0


# ── combo market creation ─────────────────────────────────────────────
def test_create_combo_market_posts_selected_markets(auth, monkeypatch):
    captured = {}

    def fake(method, url, params=None, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json.loads(data)
        return FakeResponse(201, {"market_ticker": "KXMVE-X", "event_ticker": "E"})

    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", fake)
    legs = [{"market_ticker": "A", "event_ticker": "EA", "side": "yes"},
            {"market_ticker": "B", "event_ticker": "EB", "side": "no"}]
    out = k.create_combo_market("KXMVECROSSCATEGORY-R", legs)
    assert out["market_ticker"] == "KXMVE-X"
    assert captured["body"]["selected_markets"] == legs
    assert captured["url"].endswith(
        "/multivariate_event_collections/KXMVECROSSCATEGORY-R")


# ── subaccount isolation ──────────────────────────────────────────────
# Kalshi permits ONE ACCOUNT PER PERSON, so a second account is not an option.
# Subaccounts (0-63) are API-only — invisible to the web and mobile apps — which
# is exactly what keeps manual GUI trading out of the pod's P&L.
def test_subaccount_range_is_validated(auth):
    for bad in (-1, 64, 100):
        with pytest.raises(ValueError, match="0–63"):
            KalshiPrivate(auth, subaccount=bad)
    assert KalshiPrivate(auth, subaccount=0).subaccount == 0
    assert KalshiPrivate(auth, subaccount=63).subaccount == 63


@pytest.mark.parametrize("method,path,key", [
    ("get_positions", "/portfolio/positions", "market_positions"),
    ("get_fills", "/portfolio/fills", "fills"),
    ("get_settlements", "/portfolio/settlements", "settlements"),
    ("get_orders", "/portfolio/orders", "orders"),
])
def test_portfolio_reads_are_scoped_to_the_subaccount(auth, monkeypatch,
                                                      method, path, key):
    seen = {}

    def fake(m, url, params=None, data=None, headers=None, timeout=None):
        seen["params"] = params
        return FakeResponse(200, {key: [], "cursor": None})

    k = KalshiPrivate(auth, subaccount=7)
    monkeypatch.setattr(k._s, "request", fake)
    getattr(k, method)()
    assert seen["params"]["subaccount"] == 7


def test_balance_is_scoped_to_the_subaccount(auth, monkeypatch):
    seen = {}

    def fake(m, url, params=None, data=None, headers=None, timeout=None):
        seen["params"] = params
        return FakeResponse(200, {"balance_dollars": "600.0000"})

    k = KalshiPrivate(auth, subaccount=1)
    monkeypatch.setattr(k._s, "request", fake)
    assert k.get_balance() == pytest.approx(600.0)
    assert seen["params"]["subaccount"] == 1


def test_no_subaccount_param_when_unset(auth, monkeypatch):
    """Default behaviour must be unchanged for every existing caller."""
    seen = {}

    def fake(m, url, params=None, data=None, headers=None, timeout=None):
        seen["params"] = params
        return FakeResponse(200, {"fills": [], "cursor": None})

    k = KalshiPrivate(auth)
    monkeypatch.setattr(k._s, "request", fake)
    k.get_fills()
    assert "subaccount" not in (seen["params"] or {})


def test_explicit_subaccount_argument_wins(auth, monkeypatch):
    seen = {}

    def fake(m, url, params=None, data=None, headers=None, timeout=None):
        seen["params"] = params
        return FakeResponse(200, {"fills": [], "cursor": None})

    k = KalshiPrivate(auth, subaccount=1)
    monkeypatch.setattr(k._s, "request", fake)
    k.get_fills(subaccount=0)
    assert seen["params"]["subaccount"] == 0


@pytest.mark.parametrize("call,field", [
    (lambda k: k.place_order(ticker="T", side="yes", count=1, yes_price=5), "order"),
    (lambda k: k.create_rfq("T", contracts=1), "rfq"),
    (lambda k: k.create_quote("r", yes_bid="0.05", no_bid="0.90"), "quote"),
])
def test_orders_rfqs_and_quotes_carry_the_subaccount(auth, call, field):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True, subaccount=3)
    assert call(k)[field]["subaccount"] == 3


def test_guard_queries_settlements_with_min_ts(auth):
    """Server-side filtering beats client-side: fewer rows, no pagination cap."""
    c = StubClient([])
    LossGuard(c, max_total_loss=500.0, max_daily_loss=100.0,
              since="2026-08-01T00:00:00Z").check()
    assert c.last_query.get("min_ts") == 1785542400


def test_guard_warns_when_the_client_has_no_subaccount(auth, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        LossGuard(StubClient([], subaccount=None), max_total_loss=500.0,
                  max_daily_loss=100.0, since="2026-08-01")
    assert any("subaccount" in r.message for r in caplog.records)


# ── subaccount transfers ──────────────────────────────────────────────
def test_transfer_converts_dollars_to_whole_cents(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    t = k.transfer_subaccount(from_subaccount=0, to_subaccount=1,
                              amount_dollars=600.0)["transfer"]
    assert t["amount_cents"] == 60000
    assert t["from_subaccount"] == 0 and t["to_subaccount"] == 1
    assert t["client_transfer_id"]


def test_transfer_rejects_nonsense(auth):
    k = KalshiPrivate(auth, allow_orders=True, dry_run=True)
    with pytest.raises(ValueError, match="same"):
        k.transfer_subaccount(from_subaccount=1, to_subaccount=1, amount_dollars=10)
    with pytest.raises(ValueError, match="positive"):
        k.transfer_subaccount(from_subaccount=0, to_subaccount=1, amount_dollars=0)
    with pytest.raises(ValueError, match="0–63"):
        k.transfer_subaccount(from_subaccount=0, to_subaccount=99, amount_dollars=10)


def test_transfer_requires_orders_enabled(auth):
    with pytest.raises(OrdersDisabled):
        KalshiPrivate(auth).transfer_subaccount(
            from_subaccount=0, to_subaccount=1, amount_dollars=10)


def test_get_collections_reads_the_multivariate_contracts_key(auth, monkeypatch):
    """The response key is multivariate_contracts, not collections."""
    k = KalshiPrivate(auth)
    monkeypatch.setattr(
        k._s, "request",
        lambda *a, **kw: FakeResponse(
            200, {"multivariate_contracts": [{"collection_ticker": "X"}],
                  "cursor": None}))
    assert k.get_collections()[0]["collection_ticker"] == "X"
