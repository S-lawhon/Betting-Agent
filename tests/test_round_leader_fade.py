"""
tests/test_round_leader_fade.py
───────────────────────────────
Tests for P-022 (Round-Leader Dead-Heat Fade maker).

Focus is the three behaviours that separate P-022 from the P-017M top-N fade
and that would silently corrupt the paper validation if wrong:

  * Pessimistic through-fill: a resting YES ask fills ONLY on a taker BUYING
    yes strictly THROUGH the quote (adverse-selection-inclusive).
  * Scalar settlement: result="scalar" is the $1/n dead-heat payout (carried
    in settlement_value_dollars), NOT a void — booking it as void would zero
    the exact outcome the pod trades. This is the flagged Phase-2 fix.
  * Collateral caps: per-strike and per-tournament limits actually bind.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.round_leader_fade_maker import RoundLeaderFadeMakerEngine


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


CLOSE_ISO = "2026-07-10T19:00:00Z"
CLOSE = _epoch(CLOSE_ISO)
TICKER = "KXPGAR1LEAD-GESO26-DWIL"
EVENT = "KXPGAR1LEAD-GESO26"


class FakeKalshi:
    """Stands in for KalshiPublic: orderbook, trades_since, open_markets, get."""

    def __init__(self, book=None, trades=None, settle=None):
        self.book = book if book is not None else {
            "yes_bid": 0.04, "yes_ask": 0.06, "bid_qty": 50, "ask_qty": 50}
        self.trades = trades or []
        self._settle = settle or {}

    def orderbook(self, ticker, depth=5):
        return self.book

    def trades_since(self, ticker, min_epoch, max_pages=5):
        return [t for t in self.trades if t["epoch"] > min_epoch]

    def open_markets(self, series):
        return [{
            "ticker": TICKER, "event_ticker": EVENT,
            "occurrence_datetime": CLOSE_ISO,
        }] if series == "KXPGAR1LEAD" else []

    def get(self, path):
        return {"market": self._settle} if self._settle else None


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


# The §7 per-name cap is 0.5% of bankroll, so on the live $1,000 bankroll a 5c
# fade sizes to 5 contracts. Tests about FILL and SETTLEMENT mechanics set a
# bankroll high enough that the cap does not bind, so they measure the thing
# they name; the cap tests below set it deliberately low.
UNCAPPED_BANKROLL = 1_000_000.0


def make_engine(tmp_path, kalshi, clock, **kw):
    kw.setdefault("bankroll", UNCAPPED_BANKROLL)
    return RoundLeaderFadeMakerEngine(
        kalshi=kalshi, series=("KXPGAR1LEAD",), log_dir=tmp_path,
        kill_file=tmp_path / "KILL", _now_fn=clock, **kw)


# ── Quoting window ───────────────────────────────────────────────────

def test_quotes_in_early_window(tmp_path):
    clock = Clock(CLOSE - 18 * 3600)          # 18h before close: in window
    eng = make_engine(tmp_path, FakeKalshi(), clock)
    eng.discover()
    eng.cycle()
    q = eng.books[TICKER].ask_quote
    assert q is not None
    # mid = (0.04+0.06)/2 = 0.05; ask = mid + 0.02 = 0.07
    assert abs(q.price - 0.07) < 1e-9


def test_no_new_quote_too_late(tmp_path):
    clock = Clock(CLOSE - 6 * 3600)           # 6h: past no_new_quote_h (round)
    eng = make_engine(tmp_path, FakeKalshi(), clock)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


def test_no_quote_outside_band(tmp_path):
    # mid = 0.20 -> outside the 0.03-0.10 band, no quote
    k = FakeKalshi(book={"yes_bid": 0.18, "yes_ask": 0.22,
                         "bid_qty": 10, "ask_qty": 10})
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, k, clock)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


# ── Through-fill (pessimistic, adverse-selection-inclusive) ──────────

def test_fills_only_on_taker_buy_through(tmp_path):
    t0 = CLOSE - 18 * 3600
    trades = [
        {"epoch": t0 + 60, "yes_price": 0.09, "count": 8, "taker_side": "yes"},   # through -> fill 8
        {"epoch": t0 + 120, "yes_price": 0.06, "count": 5, "taker_side": "yes"},  # not through (<=0.07)
        {"epoch": t0 + 180, "yes_price": 0.12, "count": 4, "taker_side": "no"},   # wrong side
    ]
    clock = Clock(t0)
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), clock)
    eng.discover()
    eng.cycle()                                # cycle 1: place quote
    eng.cycle()                                # cycle 2: check fills vs quote
    book = eng.books[TICKER]
    assert book.sold == 8                      # only the through taker-buy filled
    assert abs(book.fills[0].price - 0.07) < 1e-9


# ── Scalar settlement (THE FIX) ──────────────────────────────────────

def _fill_one(tmp_path, **kw):
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 10,
               "taker_side": "yes"}]
    clock = Clock(t0)
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), clock, **kw)
    eng.discover()
    eng.cycle()                                # place quote
    eng.cycle()                                # fill against it
    return eng, clock


def _settle_result(eng, clock, settle):
    eng.kalshi._settle = settle
    clock.t = CLOSE + 3600                      # past close
    eng.cycle()


def test_scalar_settles_at_split_payout_not_void(tmp_path):
    """result=scalar with settlement_value_dollars=0.20 (5-way tie) must book
    payout 0.20 -> a LOSS for the YES seller (sold 0.07, pays 0.20), NOT a
    void (which would wrongly show +0.07 profit)."""
    eng, clock = _fill_one(tmp_path)
    _settle_result(eng, clock, {"result": "scalar",
                                "settlement_value_dollars": "0.2000"})
    fill = eng.books[TICKER].fills[0]
    assert fill.settled
    # pnl = (0.07 - 0.20 - 0) * 10 = -1.30
    line = (tmp_path / "round_leader_fade_fills.jsonl").read_text().splitlines()
    settle = [l for l in line if '"SETTLE"' in l][-1]
    import json
    rec = json.loads(settle)
    assert abs(rec["payout"] - 0.20) < 1e-9
    assert rec["pnl_usd"] < 0                   # would be +0.70 if voided
    assert abs(rec["pnl_usd"] - (-1.30)) < 1e-6


def test_yes_settlement_is_full_loss(tmp_path):
    eng, clock = _fill_one(tmp_path)
    _settle_result(eng, clock, {"result": "yes"})
    import json
    rec = [json.loads(l) for l in
           (tmp_path / "round_leader_fade_fills.jsonl").read_text().splitlines()
           if '"SETTLE"' in l][-1]
    # sold 10 @ 0.07, pays 1.0 -> pnl = (0.07-1.0)*10 = -9.30
    assert abs(rec["pnl_usd"] - (-9.30)) < 1e-6


def test_no_settlement_is_full_premium(tmp_path):
    eng, clock = _fill_one(tmp_path)
    _settle_result(eng, clock, {"result": "no"})
    import json
    rec = [json.loads(l) for l in
           (tmp_path / "round_leader_fade_fills.jsonl").read_text().splitlines()
           if '"SETTLE"' in l][-1]
    # busts: keep the premium -> pnl = (0.07-0.0)*10 = +0.70
    assert abs(rec["pnl_usd"] - 0.70) < 1e-6


# ── Collateral caps ──────────────────────────────────────────────────

def test_per_name_cap_limits_fill(tmp_path):
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 100,
               "taker_side": "yes"}]
    clock = Clock(t0)
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), clock,
                      max_contracts_per_name=5)
    eng.discover()
    eng.cycle()                                 # place quote (sized to 5)
    eng.cycle()                                 # fill
    assert eng.books[TICKER].sold == 5          # capped despite 100 available


def test_tournament_collateral_cap_binds(tmp_path):
    # tiny tournament cap -> quote sized down / pulled
    clock = Clock(CLOSE - 18 * 3600)
    # 0.05% of a $1,000 bankroll = $0.50 of tournament collateral.
    eng = make_engine(tmp_path, FakeKalshi(), clock,
                      bankroll=1000.0, pct_per_tournament=0.0005,
                      pct_per_name=1.0, pct_total=1.0)
    eng.discover()
    eng.cycle()
    q = eng.books[TICKER].ask_quote
    # room_event/per_ct = 0.5/0.93 = 0.53 -> int() = 0 -> no viable quote
    assert q is None or q.qty == 0


def test_kill_switch_pulls_quote(tmp_path):
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, FakeKalshi(), clock)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None
    (tmp_path / "KILL").write_text("stop")
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


# ── close_epoch: the defect that made the pod unable to quote at all ──

class FieldKalshi(FakeKalshi):
    """open_markets() returns whatever timing fields the test specifies."""

    def __init__(self, market_fields, **kw):
        super().__init__(**kw)
        self._fields = market_fields

    def open_markets(self, series):
        if series != "KXPGAR1LEAD":
            return []
        m = {"ticker": TICKER, "event_ticker": EVENT}
        m.update(self._fields)
        return [m]


def test_close_time_wins_over_the_occurrence_placeholder(tmp_path):
    """The bug that made P-022 structurally incapable of quoting.

    ``occurrence_datetime`` is a far-future PLACEHOLDER on round-leader markets,
    not the round end. Measured 2026-07-26 on one settled market per series
    across all five tours, it ran 13.2-18.2 days LATER than ``close_time``, 10
    of 10. The pod preferred it, so close_epoch sat ~2 weeks past the real round
    end: the [12h, 24h] placement window opened long after the round had settled,
    and _mid() returns None on a settled book. It ran live 2026-07-23 -> 07-26
    and wrote zero quotes and zero fills.
    """
    placeholder = datetime.fromtimestamp(CLOSE + 16 * 86400, tz=timezone.utc)
    k = FieldKalshi({"close_time": CLOSE_ISO,
                     "occurrence_datetime": placeholder.isoformat()})
    clock = Clock(CLOSE - 18 * 3600)            # 18h before the REAL close
    eng = make_engine(tmp_path, k, clock)
    eng.discover()
    assert abs(eng.books[TICKER].close_epoch - CLOSE) < 1.0, \
        "close_epoch must track close_time, not the occurrence placeholder"
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None, \
        "with the placeholder preferred, this window never opens and P-022 never quotes"


def test_occurrence_is_still_used_when_close_time_is_absent(tmp_path):
    """Fallback preserved — some markets carry only occurrence_datetime."""
    k = FieldKalshi({"occurrence_datetime": CLOSE_ISO})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    assert abs(eng.books[TICKER].close_epoch - CLOSE) < 1.0


# ── §7 caps are GATE CONDITIONS, expressed as % of bankroll ──────────

def test_caps_are_derived_from_bankroll_percentages():
    """§7: per-name <=0.5%, per-tournament <=5%, aggregate <=15%."""
    eng = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi(), bankroll=1000.0)
    assert abs(eng.max_collateral_per_name - 5.0) < 1e-9
    assert abs(eng.max_collateral_per_tournament - 50.0) < 1e-9
    assert abs(eng.max_total_collateral - 150.0) < 1e-9
    # and they track the bankroll rather than being pinned to dollars
    eng2 = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi(), bankroll=4000.0)
    assert abs(eng2.max_collateral_per_name - 20.0) < 1e-9


def test_per_name_collateral_cap_binds_before_the_contract_cap(tmp_path):
    """The shipped 25-contract cap was a ~5x breach of the §7 per-name limit.

    At a 5c mid the quote goes out at 0.07, so worst-case collateral is $0.93 a
    contract. 25 contracts = $23.25 = 2.3% of a $1,000 bankroll against a 0.5%
    ($5) limit. Collateral must bind first: floor(5/0.93) = 5 contracts.
    """
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 100,
               "taker_side": "yes"}]
    clock = Clock(t0)
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), clock,
                      bankroll=1000.0)
    eng.discover()
    eng.cycle()
    eng.cycle()
    book = eng.books[TICKER]
    assert book.sold == 5, "per-name collateral cap must bind, not the 25-ct cap"
    assert book.collateral <= eng.max_collateral_per_name + 1e-9
    assert book.collateral <= 0.005 * 1000.0 + 1e-9


def test_band_matches_the_locked_decision_rule():
    """§1 names [0.03, 0.12]. The pod shipped (0.03, 0.10)."""
    eng = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi())
    assert eng.mid_band == (0.03, 0.12)


def test_a_mid_inside_the_rule_band_is_quoted(tmp_path):
    """mid = 0.11 sits inside [0.03, 0.12] but outside the shipped (0.03, 0.10)."""
    k = FakeKalshi(book={"yes_bid": 0.10, "yes_ask": 0.12,
                         "bid_qty": 50, "ask_qty": 50})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None


# ── §7: AggregateRiskGuard reservations ──────────────────────────────

class SpyGuard:
    """Minimal guard double: records reservations, can refuse."""

    def __init__(self, approve=True):
        self.approve = approve
        self.reserved = {}
        self.released = []

    def reserve_trade(self, pod_id, venue, market_id, usd):
        if not self.approve:
            return False
        self.reserved[market_id] = {"pod_id": pod_id, "venue": venue, "usd": usd}
        return True

    def release_reservation(self, market_id):
        self.released.append(market_id)
        self.reserved.pop(market_id, None)


class CheckOnlyGuard:
    """Older guard shape — only check_trade. Must still work (getattr-optional)."""

    def __init__(self, approve=True):
        self.approve = approve
        self.calls = []

    def check_trade(self, pod_id, venue, usd, market_id=None):
        self.calls.append(usd)
        return self.approve


def test_quote_reserves_worst_case_collateral(tmp_path):
    """The reservation must be COLLATERAL, not premium — it is what the pod
    owes if the name actually leads, which is the tail §7 exists to bound."""
    guard = SpyGuard()
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, risk_guard=guard)
    eng.discover()
    eng.cycle()
    q = eng.books[TICKER].ask_quote
    assert q is not None
    assert TICKER in guard.reserved
    expected = q.qty * (1.0 - q.price)
    assert abs(guard.reserved[TICKER]["usd"] - expected) < 1e-6
    assert guard.reserved[TICKER]["pod_id"] == "P-022"


def test_guard_refusal_blocks_the_quote(tmp_path):
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, risk_guard=SpyGuard(approve=False))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


def test_pulling_a_quote_releases_its_reservation(tmp_path):
    """Otherwise the pod starves itself one abandoned name at a time."""
    guard = SpyGuard()
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, risk_guard=guard)
    eng.discover()
    eng.cycle()
    assert TICKER in guard.reserved
    (tmp_path / "KILL").write_text("stop")
    eng.cycle()
    assert TICKER in guard.released
    assert TICKER not in guard.reserved


def test_guard_without_reserve_trade_still_works(tmp_path):
    """CLAUDE.md: optional-by-getattr at every call site."""
    guard = CheckOnlyGuard()
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, risk_guard=guard)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None
    assert guard.calls


def test_no_guard_is_a_no_op(tmp_path):
    """Caps remain the binding constraint when no guard is supplied."""
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None


def test_bankroll_is_read_from_the_live_config_key():
    """The live key is `risk.initial_bankroll`, not `risk.bankroll`.

    Reading only `bankroll` fell back to the 1000.0 default — correct today by
    coincidence, wrong the moment the paper bankroll changes. That is the same
    failure mode as the fixed-dollar caps this reconciliation removed.
    """
    from src.round_leader_fade_maker import RoundLeaderFadeMakerPod as P
    eng = P.from_config({"risk": {"initial_bankroll": 4000.0}})
    assert abs(eng.bankroll - 4000.0) < 1e-9
    assert abs(eng.max_collateral_per_name - 20.0) < 1e-9      # 0.5%
    assert abs(eng.max_collateral_per_tournament - 200.0) < 1e-9  # 5%
    assert abs(eng.max_total_collateral - 600.0) < 1e-9        # 15%


def test_pod_block_overrides_win_over_the_global_bankroll():
    from src.round_leader_fade_maker import RoundLeaderFadeMakerPod as P
    eng = P.from_config({"risk": {"initial_bankroll": 4000.0},
                         "pods": {"P-022": {"risk": {"bankroll": 250.0}}}})
    assert abs(eng.bankroll - 250.0) < 1e-9
