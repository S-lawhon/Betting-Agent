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
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.golf_schedule import RoundClose
from src.round_leader_fade_maker import (MakerFill,
                                        RoundLeaderFadeMakerEngine)


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


CLOSE_ISO = "2026-07-10T19:00:00Z"
CLOSE = _epoch(CLOSE_ISO)
TICKER = "KXPGAR1LEAD-GESO26-DWIL"
EVENT = "KXPGAR1LEAD-GESO26"


class FakeKalshi:
    """Stands in for KalshiPublic: orderbook, trades_since, open_markets, get."""

    def __init__(self, book=None, trades=None, settle=None):
        # Default book is comfortably THICK (>= the 100-contract size screen)
        # so tests about other mechanics measure the thing they name; the
        # size-screen tests below set thin books deliberately.
        self.book = book if book is not None else {
            "yes_bid": 0.04, "yes_ask": 0.06, "bid_qty": 500, "ask_qty": 500}
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

    def get(self, path, params=None):
        if path.startswith("/events/"):
            return {"event": {"product_metadata": {
                "competition": "Genesis Scottish Open",
                "competition_scope": "Round 1 Leader"}}}
        return {"market": self._settle} if self._settle else None


class FakeSchedule:
    """Stands in for GolfScheduleResolver.

    The engine has NO fallback to a Kalshi time field — every one of them is a
    ~20-day placeholder while the market is open — so a resolver is mandatory
    and every test supplies one.
    """

    _DEFAULT = object()

    def __init__(self, close_epoch=_DEFAULT, source="tee_times"):
        # `close_epoch=None` means "resolves to nothing" (the fail-closed
        # path); omitting it means "the real round end". Distinguishing the
        # two is the point — conflating them is how a fail-closed test can
        # pass while asserting nothing.
        self.close_epoch = (CLOSE if close_epoch is FakeSchedule._DEFAULT
                            else close_epoch)
        self.source = source
        self.calls = []

    def resolve(self, competition, series_ticker, scope=None, near=None):
        self.calls.append((competition, series_ticker, scope))
        if self.close_epoch is None:
            return None
        return RoundClose(
            close_epoch=self.close_epoch, source=self.source,
            competition=competition, tour="KXPGA", round_number=1,
            espn_event_id="1", espn_name=competition, lag_h=4.0)


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
    kw.setdefault("schedule", FakeSchedule())
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


def test_kalshi_time_fields_are_ignored_entirely(tmp_path):
    """The defect that made P-022 structurally incapable of quoting, twice.

    2026-07-26 changed `_close_epoch` to prefer `close_time` over
    `occurrence_datetime` on the strength of a 10-of-10 measurement — taken on
    SETTLED markets, the only state in which Kalshi has rewritten `close_time`
    to the truth. The pod only ever reads OPEN markets, where all six time
    fields collapse to the same ~20-day fallback. Measured 2026-07-27 across
    346 open markets on three tours: every field read 2026-08-16T00:00:00Z.
    Swapping one placeholder for the identical placeholder fixed nothing, and
    the pod went a further two days without a quote.

    So the close is now resolved EXTERNALLY and the Kalshi fields are not read
    at all. This test feeds the engine a `close_time` two weeks out — the real
    live payload — and asserts the pod quotes anyway.
    """
    placeholder = datetime.fromtimestamp(CLOSE + 20 * 86400, tz=timezone.utc)
    k = FieldKalshi({"close_time": placeholder.isoformat(),
                     "occurrence_datetime": placeholder.isoformat(),
                     "expiration_time": placeholder.isoformat()})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    assert abs(eng.books[TICKER].close_epoch - CLOSE) < 1.0, \
        "close_epoch must come from the schedule resolver, not any Kalshi field"
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None, \
        "with a Kalshi field as the reference this window never opens"


def test_unresolvable_event_is_never_quoted(tmp_path):
    """Fail closed. A wrong round time is worse than no quote: it produces
    fills outside the validated window, and §7 excludes those tournaments
    from T anyway."""
    k = FieldKalshi({"close_time": CLOSE_ISO})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600),
                      schedule=FakeSchedule(close_epoch=None))
    assert eng.discover() == 0
    assert eng.books == {}
    assert EVENT in eng.unresolved_events
    eng.cycle()
    assert eng.books == {}


def test_close_is_re_resolved_as_better_timing_arrives(tmp_path):
    """ESPN publishes no tee times three days out — checked 2026-07-27 against
    all three listed tournaments, all returned zero competitors. A market
    discovered at listing therefore gets the coarse per-tour day offset and
    must be UPGRADED to the precise tee-time close once ESPN publishes it. A
    close resolved once at discovery would freeze the coarse answer."""
    sched = FakeSchedule(close_epoch=CLOSE + 5 * 3600,
                         source="tour_day_offset")
    k = FieldKalshi({"close_time": CLOSE_ISO})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600), schedule=sched)
    eng.discover()
    assert eng.books[TICKER].close_source == "tour_day_offset"
    sched.close_epoch, sched.source = CLOSE, "tee_times"
    eng.discover()
    assert abs(eng.books[TICKER].close_epoch - CLOSE) < 1.0
    assert eng.books[TICKER].close_source == "tee_times"


def test_a_full_field_re_resolve_logs_once_per_event(tmp_path, caplog):
    """The close is EVENT-level but discover() walks per market, and on
    2026-08-12 one tour_day_offset -> tee_times upgrade for BOC26 emitted the
    identical INFO line ~80 times in a single pass — most of the unit's
    journal volume. One upgrade, one line; every book still upgraded."""

    class FullFieldKalshi(FakeKalshi):
        def open_markets(self, series):
            if series != "KXPGAR1LEAD":
                return []
            return [{"ticker": f"{EVENT}-N{i:02d}", "event_ticker": EVENT,
                     "close_time": CLOSE_ISO} for i in range(80)]

    sched = FakeSchedule(close_epoch=CLOSE + 5 * 3600,
                         source="tour_day_offset")
    eng = make_engine(tmp_path, FullFieldKalshi(), Clock(CLOSE - 18 * 3600),
                      schedule=sched)
    eng.discover()
    sched.close_epoch, sched.source = CLOSE, "tee_times"
    with caplog.at_level(logging.INFO, logger="src.round_leader_fade_maker"):
        eng.discover()
    lines = [r for r in caplog.records if "re-resolved" in r.getMessage()]
    assert len(lines) == 1
    assert len(eng.books) == 80
    assert all(b.close_source == "tee_times" for b in eng.books.values())
    assert all(abs(b.close_epoch - CLOSE) < 1.0 for b in eng.books.values())


# ── §7 caps are GATE CONDITIONS, expressed as % of bankroll ──────────

def test_caps_are_derived_from_bankroll_percentages():
    """§7: per-name <=0.5%, per-tournament <=5%, aggregate <=15%."""
    eng = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi(), bankroll=1000.0,
                                     schedule=FakeSchedule())
    assert abs(eng.max_collateral_per_name - 5.0) < 1e-9
    assert abs(eng.max_collateral_per_tournament - 50.0) < 1e-9
    assert abs(eng.max_total_collateral - 150.0) < 1e-9
    # and they track the bankroll rather than being pinned to dollars
    eng2 = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi(), bankroll=4000.0,
                                      schedule=FakeSchedule())
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
    eng = RoundLeaderFadeMakerEngine(kalshi=FakeKalshi(), schedule=FakeSchedule())
    assert eng.mid_band == (0.03, 0.12)


def test_a_mid_inside_the_rule_band_is_quoted(tmp_path):
    """mid = 0.11 sits inside [0.03, 0.12] but outside the shipped (0.03, 0.10)."""
    k = FakeKalshi(book={"yes_bid": 0.10, "yes_ask": 0.12,
                         "bid_qty": 500, "ask_qty": 500})
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


# ── §7's guard precondition, actually wired (2026-07-27) ─────────────
#
# The mechanism above was implemented and tested from day one, but NOTHING
# EVER CONSTRUCTED A GUARD: the runner calls from_config, from_config never
# passed one, so `risk_guard` was None in the live process and _reserve()
# returned an unconditional True. Rule §11c recorded §7 as satisfied.
#
# The second half of the fix matters more than the first: a standalone loop
# has no update_post_cycle to drain reservations, so a naive wiring would
# ratchet exposure until the guard refused everything — muting the pod
# permanently, which is worse than not wiring it at all.

def test_from_config_wires_a_real_aggregate_risk_guard():
    """The regression. `risk_guard` was None in the live process."""
    from src.aggregate_risk import AggregateRiskGuard
    from src.round_leader_fade_maker import RoundLeaderFadeMakerPod as P
    eng = P.from_config({"risk": {"initial_bankroll": 1000.0}})
    assert isinstance(eng.risk_guard, AggregateRiskGuard)
    assert callable(getattr(eng.risk_guard, "reserve_trade", None))


def test_from_config_honours_an_explicit_risk_guard_override():
    from src.round_leader_fade_maker import RoundLeaderFadeMakerPod as P
    spy = SpyGuard()
    assert P.from_config({}, risk_guard=spy).risk_guard is spy
    assert P.from_config({}, risk_guard=None).risk_guard is None


def test_settlement_releases_the_reservation(tmp_path):
    """A settled market owes nothing further. If its collateral stayed
    reserved, P-022 would starve one tournament at a time."""
    guard = SpyGuard()
    eng, clock = _fill_one(tmp_path, bankroll=1000.0, risk_guard=guard)
    assert TICKER in guard.reserved
    _settle_result(eng, clock, {"result": "no"})
    assert TICKER not in guard.reserved
    assert TICKER in guard.released
    assert TICKER not in eng._reserved_ids


def test_reservation_covers_filled_contracts_too(tmp_path):
    """Reservations are idempotent by market_id, so re-quoting with only the
    NEW quote's collateral would replace — and so erase — the exposure of
    everything already filled on that name."""
    guard = SpyGuard()
    eng, clock = _fill_one(tmp_path, bankroll=1000.0, risk_guard=guard)
    book = eng.books[TICKER]
    assert book.collateral > 0                      # something is filled
    eng.cycle()                                     # re-quote the remainder
    held = guard.reserved[TICKER]["usd"]
    assert held >= book.collateral - 1e-9


def test_sweep_releases_a_reservation_whose_book_is_gone(tmp_path):
    """Backstop: nothing may hold exposure for a market the pod no longer
    tracks — the standalone loop has no update_post_cycle to sweep for it."""
    guard = SpyGuard()
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, risk_guard=guard)
    eng.discover()
    eng.cycle()
    assert TICKER in guard.reserved
    eng.books.clear()
    eng.cycle()
    assert TICKER not in guard.reserved
    assert not eng._reserved_ids


class FakeMultiKalshi(FakeKalshi):
    """Many distinct names in one event.

    A single-ticker double cannot exercise this: reservations are keyed by
    market_id, so re-quoting one ticker replaces rather than accumulates, and
    exposure never builds up.
    """

    def __init__(self, tickers, **kw):
        super().__init__(**kw)
        self.tickers = list(tickers)

    def open_markets(self, series):
        return [{"ticker": t, "event_ticker": EVENT,
                 "occurrence_datetime": CLOSE_ISO} for t in self.tickers]


def test_reservations_drain_so_the_pod_can_still_quote_later(tmp_path):
    """THE starvation regression, against a REAL AggregateRiskGuard.

    Quote a full tournament, settle it, then quote another. So round 2 fits
    ONLY if round 1's collateral was released on settlement. If it were not,
    the guard would refuse everything from then on and the pod would go silent
    forever — the exact failure this whole workstream exists to detect,
    reintroduced by the fix meant to satisfy §7.

    The guard is deliberately given a SMALLER bankroll than the pod. Since
    quoted collateral began counting against §7's per-tournament cap, one
    tournament reserves at most $50 rather than the ~$150 it used to, and
    against the guard's own $1,000 that leaves so much headroom that round 2
    would fit whether or not round 1 drained — the test would pass for the
    wrong reason. At a $300 guard bankroll the per-pod limit is $75: one
    tournament fits, two do not.
    """
    from src.aggregate_risk import AggregateRiskGuard
    guard = AggregateRiskGuard(bankroll=300.0, max_open_positions=200)
    clock = Clock(CLOSE - 18 * 3600)
    r1 = [f"{EVENT}-N{i}" for i in range(40)]
    eng = make_engine(tmp_path, FakeMultiKalshi(r1), clock,
                      bankroll=1000.0, risk_guard=guard)
    eng.discover()
    eng.cycle()
    quoted_1 = [t for t in r1 if eng.books[t].ask_quote is not None]
    reserved_1 = sum(guard._reservations[t]["usd"] for t in eng._reserved_ids)
    assert len(quoted_1) > 10
    # §7's per-tournament cap, honoured on QUOTED collateral. Two tournaments'
    # worth would exceed the guard's $75 per-pod limit.
    assert 37.5 < reserved_1 <= 50.0

    # settle the whole tournament
    clock.t = CLOSE + 3600
    eng.kalshi._settle = {"result": "no"}
    eng.cycle()
    assert not eng._reserved_ids, "settled tournament still holds collateral"

    # a second tournament must be quotable on the same guard
    clock.t = CLOSE - 18 * 3600
    eng.kalshi.tickers = [f"{EVENT}-M{i}" for i in range(40)]
    eng.discover()
    eng.cycle()
    quoted_2 = [t for t in eng.kalshi.tickers
                if eng.books[t].ask_quote is not None]
    assert len(quoted_2) == len(quoted_1), (
        f"guard starved the pod after one settled tournament: "
        f"{len(quoted_2)} names quoted vs {len(quoted_1)}")


def test_live_guard_does_not_bind_before_the_section_7_caps(tmp_path):
    """Behaviour-neutrality: wiring the guard must not change which names get
    quoted, or it would alter the population under test mid-gate. P-022's own
    caps (aggregate 15% of bankroll) are strictly tighter than the guard's
    (25% per pod, 30% per venue, 50% total)."""
    from src.aggregate_risk import AggregateRiskGuard
    cfg = {"risk": {"initial_bankroll": 1000.0},
           "aggregate_risk": {"max_total_exposure_pct": 0.50,
                              "max_venue_exposure_pct": 0.30,
                              "max_pod_exposure_pct": 0.25,
                              "max_open_positions": 200}}
    clock_a, clock_b = Clock(CLOSE - 18 * 3600), Clock(CLOSE - 18 * 3600)
    unguarded = make_engine(tmp_path, FakeKalshi(), clock_a, bankroll=1000.0)
    guarded = make_engine(tmp_path, FakeKalshi(), clock_b, bankroll=1000.0,
                          risk_guard=AggregateRiskGuard.from_config(cfg))
    for eng in (unguarded, guarded):
        eng.discover()
        eng.cycle()
    qa, qb = unguarded.books[TICKER].ask_quote, guarded.books[TICKER].ask_quote
    assert qa is not None and qb is not None
    assert abs(qa.price - qb.price) < 1e-9
    assert abs(qa.qty - qb.qty) < 1e-9


# ── §7 hole (a): books must survive a restart ────────────────────────

def test_restart_restores_unsettled_fills_so_caps_do_not_re_arm(tmp_path):
    """The live §7 breach path.

    `self.books = {}` on init and nothing ever read the fills log back, so
    after any restart — deploy, reboot, crash — `book.collateral` was 0 for
    names the pod was still short and all three collateral caps re-armed from
    zero while the paper exposure persisted. A restart mid-tournament could
    silently double the per-name and per-tournament collateral. The service
    was restarted twice in the week this was found, and under §7 a breach
    EXCLUDES the tournament from T.
    """
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 100,
               "taker_side": "yes"}]
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), Clock(t0),
                      bankroll=1000.0)
    eng.discover()
    eng.cycle()
    eng.cycle()
    sold = eng.books[TICKER].sold
    collateral = eng.books[TICKER].collateral
    assert sold > 0

    # a fresh process over the same logs
    eng2 = make_engine(tmp_path, FakeKalshi(), Clock(t0 + 120),
                       bankroll=1000.0)
    assert eng2.rebuild_from_log() == len(eng.books[TICKER].fills)
    assert eng2.books[TICKER].sold == sold
    assert abs(eng2.books[TICKER].collateral - collateral) < 1e-9
    assert eng2.books[TICKER].inventory == -sold


def test_restart_does_not_restore_settled_fills(tmp_path):
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 100,
               "taker_side": "yes"}]
    k = FakeKalshi(trades=trades, settle={"result": "no"})
    eng = make_engine(tmp_path, k, Clock(t0), bankroll=1000.0)
    eng.discover()
    eng.cycle()
    eng.cycle()
    eng._now = Clock(CLOSE + 60)
    eng.cycle()
    assert eng.books[TICKER].done

    eng2 = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE + 120),
                       bankroll=1000.0)
    assert eng2.rebuild_from_log() == 0


def test_a_restored_book_is_never_treated_as_quotable_without_a_schedule(tmp_path):
    """A restored book whose market is gone has no schedule anchor. It must
    default to 'past close' (settle), never to a quotable window."""
    (tmp_path / "round_leader_fade_fills.jsonl").write_text(
        json.dumps({"type": "FILL", "fill_id": "X1", "pod_id": "P-022",
                    "ticker": TICKER, "event": "GESO26", "price": 0.07,
                    "qty": 3, "ts": CLOSE - 3600}) + "\n")
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600))
    assert eng.rebuild_from_log() == 1
    assert eng.books[TICKER].close_epoch == 0.0
    assert eng.books[TICKER].close_source == "restored"


# ── §7 hole (b): a cap breach must be RECORDED when it happens ───────

def test_cap_breach_is_recorded_to_the_log(tmp_path):
    """§7: 'a tournament run with any cap breached is EXCLUDED from T'.
    That requires the breach to be recorded when it happens — a gate
    condition only checkable retrospectively is not a gate condition."""
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0)
    eng.discover()
    book = eng.books[TICKER]
    # simulate exposure that a restart-blind process could have accumulated
    book.fills.append(MakerFill(fill_id="X1", ticker=TICKER, price=0.05,
                                qty=40, epoch=CLOSE - 3600, mid_at_fill=0.05))
    eng.check_caps()
    assert ("GESO26", "per_name") in eng._breaches
    rows = [json.loads(l) for l in
            (tmp_path / "round_leader_fade_fills.jsonl").read_text().splitlines()]
    breaches = [r for r in rows if r["type"] == "CAP_BREACH"]
    assert breaches and breaches[0]["event"] == "GESO26"
    assert breaches[0]["kind"] == "per_name"
    # recorded once, not once per cycle
    eng.check_caps()
    eng.check_caps()
    rows = [json.loads(l) for l in
            (tmp_path / "round_leader_fade_fills.jsonl").read_text().splitlines()]
    assert sum(1 for r in rows
               if r["type"] == "CAP_BREACH" and r["kind"] == "per_name") == 1


def test_recorded_breaches_survive_a_restart(tmp_path):
    (tmp_path / "round_leader_fade_fills.jsonl").write_text(
        json.dumps({"type": "CAP_BREACH", "pod_id": "P-022",
                    "event": "GESO26", "kind": "per_tournament",
                    "observed": 90.0, "limit": 50.0}) + "\n")
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE))
    eng.rebuild_from_log()
    assert "GESO26" in eng.breached_events


def test_no_breach_recorded_when_caps_hold(tmp_path):
    t0 = CLOSE - 18 * 3600
    trades = [{"epoch": t0 + 60, "yes_price": 0.09, "count": 100,
               "taker_side": "yes"}]
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), Clock(t0),
                      bankroll=1000.0)
    eng.discover()
    eng.cycle()
    eng.cycle()
    assert eng._breaches == set()


# ── a conservative close must not stop the book working ──────────────

def test_passing_the_resolved_close_does_not_stop_processing_fills(tmp_path):
    """The resolved close is deliberately EARLY (median +1.6h on tee times,
    +5.6h on the day offset, up to +52h on a weather suspension). The old code
    returned as soon as `now >= close_epoch`, which would have stopped
    processing fills for the rest of the round on a quote that is supposed to
    REST through it — and that is where Phase 2's fills, and its edge, come
    from."""
    t0 = CLOSE - 18 * 3600
    late = CLOSE + 1800                      # after the CONSERVATIVE close
    trades = [{"epoch": late, "yes_price": 0.09, "count": 4,
               "taker_side": "yes"}]
    k = FakeKalshi(trades=trades)            # get() -> no settlement yet
    eng = make_engine(tmp_path, k, Clock(t0), bankroll=1000.0)
    eng.discover()
    eng.cycle()                              # rest a quote inside the window
    assert eng.books[TICKER].ask_quote is not None
    eng._now = Clock(late + 60)
    eng.cycle()
    book = eng.books[TICKER]
    assert not book.done
    assert book.sold > 0, "a resting quote must keep filling past the " \
                          "conservative close until the market really settles"


# ── §7: the cap binds on QUOTED collateral, not just filled ──────────
#
# A resting sell-YES quote is an unconditional obligation, so it consumes the
# per-tournament cap the moment it rests. Sizing used to subtract FILLED
# collateral only, so every name in a tournament's window saw a book that
# looked empty. Measured against live Kalshi data at AIGWO26 R1's real
# window-open instant: 24 quotes, $111.65 of worst-case collateral, against a
# $50 (5% of $1,000) per-tournament limit, with NO breach recorded because
# nothing had filled. Under §7 that would have excluded the first tournament
# of a 24-tournament gate.


def test_quoted_collateral_counts_against_the_tournament_cap(tmp_path):
    """The regression. 40 in-band names, one tournament, nothing filled."""
    clock = Clock(CLOSE - 18 * 3600)
    names = [f"{EVENT}-N{i:02d}" for i in range(40)]
    eng = make_engine(tmp_path, FakeMultiKalshi(names), clock, bankroll=1000.0)
    eng.discover()
    eng.cycle()

    quoted = [t for t in names if eng.books[t].ask_quote is not None]
    assert quoted, "the pod must still quote — this is a cap, not a mute"
    assert len(quoted) < len(names), "the cap must actually drop names"

    # Nothing has filled, so the OLD accounting would read zero here.
    assert eng.tournament_collateral(eng.books[quoted[0]].event_code) == 0.0
    exposure = eng.tournament_exposure(eng.books[quoted[0]].event_code)
    assert exposure <= eng.max_collateral_per_tournament + 1e-9, (
        f"§7 per-tournament cap breached on quoted collateral: "
        f"${exposure:.2f} > ${eng.max_collateral_per_tournament:.2f}")
    assert eng.total_exposure() <= eng.max_total_collateral + 1e-9
    assert not eng._breaches, "sizing must prevent the breach, not record it"


def test_a_quote_does_not_shrink_itself_on_the_next_cycle(tmp_path):
    """The ratchet the self-exclusion exists to prevent.

    A re-price REPLACES the resting quote, so the book's own quoted collateral
    must not be counted against the room available to it. If it were, every
    cycle would size the quote against its own exposure and walk it down to
    zero — a mute that looks exactly like a cap working.
    """
    clock = Clock(CLOSE - 18 * 3600)
    names = [f"{EVENT}-N{i:02d}" for i in range(40)]
    eng = make_engine(tmp_path, FakeMultiKalshi(names), clock, bankroll=1000.0)
    eng.discover()
    eng.cycle()
    first = {t: eng.books[t].ask_quote.qty for t in names
             if eng.books[t].ask_quote is not None}
    assert first
    for _ in range(5):
        eng.cycle()
    after = {t: (eng.books[t].ask_quote.qty if eng.books[t].ask_quote else 0.0)
             for t in first}
    assert after == first, (
        f"quotes ratcheted down over repeated cycles: {first} -> {after}")


def test_allocation_order_is_ticker_not_dict_order(tmp_path):
    """The drop rule is greedy in ticker order and must not depend on the
    order Kalshi happened to list the markets in."""
    clock = Clock(CLOSE - 18 * 3600)
    names = [f"{EVENT}-N{i:02d}" for i in range(40)]

    eng_a = make_engine(tmp_path / "a", FakeMultiKalshi(list(names)), clock,
                        bankroll=1000.0)
    eng_a.discover()
    eng_a.cycle()
    eng_b = make_engine(tmp_path / "b", FakeMultiKalshi(list(reversed(names))),
                        clock, bankroll=1000.0)
    eng_b.discover()
    eng_b.cycle()

    def placed(e):
        return {t: e.books[t].ask_quote.qty for t in names
                if e.books[t].ask_quote is not None}

    assert placed(eng_a) == placed(eng_b)
    # and it is a PREFIX in ticker order, not an arbitrary subset
    got = sorted(placed(eng_a))
    assert got == sorted(names)[:len(got)]


def test_a_fill_does_not_change_exposure(tmp_path):
    """Exposure must be invariant across a fill, or the cap could be breached
    by a quote merely being lifted — with no new quote placed anywhere."""
    clock = Clock(CLOSE - 18 * 3600)
    trades = [{"epoch": CLOSE - 17 * 3600, "yes_price": 0.09,
               "count": 3, "taker_side": "yes"}]
    eng = make_engine(tmp_path, FakeKalshi(trades=trades), clock,
                      bankroll=1000.0)
    eng.discover()
    eng.cycle()                                  # place
    book = eng.books[TICKER]
    before = book.exposure
    assert book.quoted_collateral > 0 and book.collateral == 0
    eng.cycle()                                  # fill 3 of them
    assert book.collateral > 0, "the fill must have happened"
    assert abs(book.exposure - before) < 1e-9, (
        f"exposure moved on a fill: {before} -> {book.exposure}")


def test_cap_breach_is_recorded_against_quoted_collateral(tmp_path):
    """check_caps must observe the same quantity the caps bind against.

    Hand-place an over-cap quote (bypassing sizing) and assert the breach is
    seen. Observing filled collateral only is what made a $111.65 book on a
    $50 limit read as clean.
    """
    from src.round_leader_fade_maker import MakerQuote
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, FakeKalshi(), clock, bankroll=1000.0)
    eng.discover()
    book = eng.books[TICKER]
    book.ask_quote = MakerQuote(price=0.07, qty=1000.0,
                                active_from=clock(), mid_at_quote=0.05)
    assert book.collateral == 0.0
    eng.check_caps()
    assert eng._breaches, "an over-cap resting quote must record a §7 breach"
    kinds = {k for _, k in eng._breaches}
    assert "per_tournament" in kinds and "per_name" in kinds


# ── observability: what book was each quote priced off? ──────────────
#
# Measured 2026-07-28 on AIGWO26 R1: 143 of the event's 146 markets carry NO
# resting YES bid at any price, so `_mid`'s one-sided branch drives essentially
# every live placement. The backtest never priced off a one-sided ask —
# `quirks_common.candle_price` rejects one outright — so the provenance of each
# live quote has to be in the log to be adjudicated later.


def test_quote_records_the_book_it_was_priced_off_two_sided(tmp_path):
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, FakeKalshi(), clock)
    eng.discover()
    eng.cycle()
    rec = [json.loads(l) for l in
           (tmp_path / "round_leader_fade_quotes.jsonl").read_text().splitlines()
           if '"QUOTE"' in l][-1]
    assert rec["book_side"] == "two_sided"
    assert rec["yes_bid"] == 0.04 and rec["yes_ask"] == 0.06
    assert rec["ask_qty"] == 500
    assert rec["close_source"] == "tee_times"


def test_quote_records_a_one_sided_ask_reference(tmp_path):
    """The live case. yes_bid absent -> the reference IS the ask, and the row
    must say so; `mid` alone cannot distinguish 0.05 the mid from 0.05 the
    ask, and those are different claims about what was quoted."""
    k = FakeKalshi(book={"yes_bid": None, "yes_ask": 0.05,
                         "bid_qty": 0, "ask_qty": 10})
    clock = Clock(CLOSE - 18 * 3600)
    # min_top_size=0: this test measures reference-price observability, not
    # the size screen; the thin fixture is deliberate.
    eng = make_engine(tmp_path, k, clock, min_top_size=0)
    eng.discover()
    eng.cycle()
    q = eng.books[TICKER].ask_quote
    assert q is not None
    # the arithmetic: reference = the ASK (0.05), quote = ask + 0.02 = 0.07
    assert abs(q.mid_at_quote - 0.05) < 1e-9
    assert abs(q.price - 0.07) < 1e-9
    rec = [json.loads(l) for l in
           (tmp_path / "round_leader_fade_quotes.jsonl").read_text().splitlines()
           if '"QUOTE"' in l][-1]
    assert rec["book_side"] == "one_sided_ask"
    assert rec["yes_bid"] is None and rec["yes_ask"] == 0.05
    assert rec["ask_qty"] == 10
    assert abs(rec["mid"] - 0.05) < 1e-9


def test_one_sided_reference_is_the_ask_not_a_none_coerced_mid(tmp_path):
    """A None-to-zero coercion would make the reference (0 + ask)/2 = ask/2.
    Pin the difference: at ask 0.08 that would be 0.04 and quote 0.06, versus
    the intended 0.08 -> 0.10."""
    k = FakeKalshi(book={"yes_bid": None, "yes_ask": 0.08,
                         "bid_qty": 0, "ask_qty": 3})
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, k, clock, min_top_size=0)
    eng.discover()
    eng.cycle()
    q = eng.books[TICKER].ask_quote
    assert abs(q.mid_at_quote - 0.08) < 1e-9, "reference must be the ask itself"
    assert abs(q.price - 0.10) < 1e-9


# ── R5 size screen: TOP-OF-BOOK SIZE, not spread ─────────────────────
# CONTROLH-2026-R carried 152,862 contracts at bid; KXRHOUSESEATS-27-230
# carried 1. Both passed a "spread <= 2c" test. Size is the only thing that
# separates a real reference price from a phantom one, so the screen is on
# size and the threshold is config.

def test_thin_one_sided_book_is_refused(tmp_path):
    """The live case: no YES bid, a 1-lot ask. The mid is 'priceable' but the
    reference has nothing behind it — no quote may rest on it."""
    k = FakeKalshi(book={"yes_bid": None, "yes_ask": 0.05,
                         "bid_qty": 0, "ask_qty": 1})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None
    recs = [json.loads(l) for l in
            (tmp_path / "round_leader_fade_quotes.jsonl").read_text().splitlines()]
    refusals = [r for r in recs if r.get("type") == "REFUSED"]
    assert len(refusals) == 1 and refusals[0]["reason"] == "thin_book"
    assert refusals[0]["ask_qty"] == 1
    # the refusal is logged once, not once per cycle
    eng.cycle()
    recs2 = [json.loads(l) for l in
             (tmp_path / "round_leader_fade_quotes.jsonl").read_text().splitlines()
             if json.loads(l).get("type") == "REFUSED"]
    assert len(recs2) == 1


def test_thin_two_sided_book_is_refused_on_either_side(tmp_path):
    """A two-sided mid leans on both sides, so BOTH must carry size — a thick
    ask cannot vouch for a 1-lot bid (the CONTROLH/HOUSESEATS asymmetry)."""
    k = FakeKalshi(book={"yes_bid": 0.04, "yes_ask": 0.06,
                         "bid_qty": 1, "ask_qty": 152_862})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


def test_thick_book_passes_the_screen(tmp_path):
    k = FakeKalshi(book={"yes_bid": 0.04, "yes_ask": 0.06,
                         "bid_qty": 100, "ask_qty": 100})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None


def test_screen_gates_placement_not_resting_quotes(tmp_path):
    """The locked strategy rests quotes through the round; the screen may only
    refuse NEW placement. A book that thins after the quote rests must not
    pull it."""
    k = FakeKalshi(book={"yes_bid": 0.04, "yes_ask": 0.06,
                         "bid_qty": 500, "ask_qty": 500})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None
    k.book = {"yes_bid": 0.04, "yes_ask": 0.06, "bid_qty": 1, "ask_qty": 1}
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None, \
        "a resting quote must survive the book thinning"


def test_missing_quantities_count_as_thin(tmp_path):
    """An unreadable size is not evidence of size."""
    k = FakeKalshi(book={"yes_bid": 0.04, "yes_ask": 0.06,
                         "bid_qty": None, "ask_qty": None})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600))
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None


def test_min_top_size_zero_disables_the_screen(tmp_path):
    k = FakeKalshi(book={"yes_bid": 0.04, "yes_ask": 0.06,
                         "bid_qty": 1, "ask_qty": 1})
    eng = make_engine(tmp_path, k, Clock(CLOSE - 18 * 3600), min_top_size=0)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None


def test_min_top_size_comes_from_config(tmp_path):
    from src.round_leader_fade_maker import RoundLeaderFadeMakerPod as P
    eng = P.from_config({"risk": {"initial_bankroll": 4000.0},
                         "pods": {"P-022": {"quoting": {"min_top_size": 250}}}},
                        risk_guard=None, schedule=FakeSchedule())
    assert eng.min_top_size == 250.0
    eng2 = P.from_config({"risk": {"initial_bankroll": 4000.0}},
                         risk_guard=None, schedule=FakeSchedule())
    assert eng2.min_top_size == 100.0, "default must be the registered 100"


# ── cap refusals are VISIBLE (2026-07-30: MBRE/RGER skipped in silence) ──
#
# `_pull` writes a PULL row only when a quote was resting, so a name the caps
# refused BEFORE its first quote left no trace at all. That silence is
# indistinguishable from the dead-pod failure the window detector pages on,
# and diagnosing it took an investigation the log should have answered.

def _rows(tmp_path, type_):
    path = tmp_path / "round_leader_fade_quotes.jsonl"
    if not path.exists():
        return []
    return [r for r in (json.loads(ln) for ln in
                        path.read_text().splitlines() if ln.strip())
            if r.get("type") == type_]


def test_cap_refusal_before_first_quote_writes_one_refused_row(tmp_path):
    # 0.05% of $1,000 = $0.50 tournament cap -> sizes to zero, never quotes.
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, pct_per_tournament=0.0005,
                      pct_per_name=1.0, pct_total=1.0)
    eng.discover()
    eng.cycle()
    eng.cycle()          # second cycle: same refusal, must NOT log again
    refused = _rows(tmp_path, "REFUSED")
    assert len(refused) == 1, refused
    assert refused[0]["reason"] == "cap_sized_to_zero"
    assert refused[0]["ticker"] == TICKER
    # the room figures are recorded so the refusal is adjudicable from the log
    assert "room_event" in refused[0]
    assert _rows(tmp_path, "PULL") == [], "no quote rested, nothing to pull"


def test_refusal_episode_clears_when_the_name_is_quoted(tmp_path):
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600),
                      bankroll=1000.0, pct_per_tournament=0.0005,
                      pct_per_name=1.0, pct_total=1.0)
    eng.discover()
    eng.cycle()
    assert len(_rows(tmp_path, "REFUSED")) == 1
    # cap raised -> the name quotes, which ends the refusal episode
    eng.max_collateral_per_tournament = 50.0
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None
    # cap cut again -> the RESTING quote is pulled (PULL row carries the
    # reason), and the pull marks the episode as already logged
    eng.max_collateral_per_tournament = 0.5
    eng.cycle()
    assert eng.books[TICKER].ask_quote is None
    assert len(_rows(tmp_path, "PULL")) == 1
    assert len(_rows(tmp_path, "REFUSED")) == 1, \
        "a refusal already told by the PULL row must not also write REFUSED"


def test_mark_restart_writes_a_marker_row(tmp_path):
    eng = make_engine(tmp_path, FakeKalshi(), Clock(CLOSE - 18 * 3600))
    eng.mark_restart(restored_fills=3)
    rows = _rows(tmp_path, "RESTART")
    assert len(rows) == 1
    assert rows[0]["restored_fills"] == 3


def test_settling_an_unfilled_book_pulls_its_quote_through_the_log(tmp_path):
    """An unfilled book writes no SETTLE rows (they are per-fill), so without
    an explicit PULL its resting quote looks live forever to any log replay —
    the window detector's resting-exposure reconstruction overstated AIGWO26
    by ~3x the tournament cap this way on 2026-07-30."""
    clock = Clock(CLOSE - 18 * 3600)
    eng = make_engine(tmp_path, FakeKalshi(), clock)
    eng.discover()
    eng.cycle()
    assert eng.books[TICKER].ask_quote is not None
    assert eng.books[TICKER].fills == []
    _settle_result(eng, clock, {"result": "no"})
    assert eng.books[TICKER].done
    pulls = _rows(tmp_path, "PULL")
    assert len(pulls) == 1
    assert pulls[0]["ticker"] == TICKER
    assert pulls[0]["reason"] == "settled"
