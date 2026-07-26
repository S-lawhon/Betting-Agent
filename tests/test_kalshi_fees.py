import pytest
from src.kalshi_fees import (fee_per_contract, fee_total, fee_fraction_of_stake,
                             net_edge, should_bet, series_maker_charges_fee,
                             _SERIES_MAKER_FEE)

def test_fee_peaks_at_50c():
    # per-contract fee is maximised at P=0.5
    f50 = fee_per_contract(0.5)
    assert f50 == pytest.approx(0.0175)
    assert fee_per_contract(0.5) > fee_per_contract(0.3)
    assert fee_per_contract(0.5) > fee_per_contract(0.7)
    assert fee_per_contract(0.05) < 0.005          # near-zero at tails

def test_maker_is_quarter_of_taker():
    assert fee_per_contract(0.5, maker=True) == pytest.approx(0.0175 * 0.25)

def test_fee_total_rounds_up_to_cent():
    # 100 contracts @ 0.50 => 0.07*100*0.25 = 1.75 exactly
    assert fee_total(100, 0.5) == pytest.approx(1.75)
    # a value needing round-up
    assert fee_total(1, 0.5) == pytest.approx(0.02)   # 0.0175 -> 0.02

def test_fee_fraction_higher_for_dogs():
    assert fee_fraction_of_stake(0.30) > fee_fraction_of_stake(0.70)
    assert fee_fraction_of_stake(0.5) == pytest.approx(0.035)

def test_net_edge_gate():
    # 5pp gross edge at 50c, taker: 0.05 - 0.0175 = 0.0325 > 0
    assert net_edge(0.55, 0.50) == pytest.approx(0.0325)
    # 1pp gross edge does NOT clear taker fee
    assert net_edge(0.51, 0.50) < 0
    # ...but a 55-70c favourite with 4pp edge clears comfortably as maker
    assert should_bet(0.62, 0.58, maker=True)
    assert not should_bet(0.51, 0.50, maker=False)

def test_half_spread_reduces_edge():
    assert net_edge(0.55, 0.50, half_spread=0.01) < net_edge(0.55, 0.50)


# ---------------------------------------------------------------------------
# Series-aware maker fees. Ground truth = per-series `fee_type` from
#   GET https://api.elections.kalshi.com/trade-api/v2/series/?category=Sports
# read live on 2026-07-20. quadratic => maker 0; quadratic_with_maker_fees =>
# maker 0.0175*P*(1-P).
# ---------------------------------------------------------------------------

# fee_type == "quadratic_with_maker_fees"
MLB_MAKER_FEE_SERIES = [
    "KXMLBGAME", "KXMLB", "KXMLBAL", "KXMLBNL", "KXMLBASGAME", "KXMLBHRDERBY",
]
# fee_type == "quadratic"  (ZERO maker fee)
MLB_ZERO_MAKER_SERIES = [
    "KXMLBHIT", "KXMLBKS", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL",
    "KXMLBTB", "KXMLBHR", "KXMLBHRR", "KXMLBSB", "KXMLBRFI", "KXMLBF5",
]


@pytest.mark.parametrize("series", MLB_MAKER_FEE_SERIES)
def test_mlb_outcome_series_charge_maker_fees(series):
    assert series_maker_charges_fee(series) is True
    assert fee_per_contract(0.5, maker=True, series_ticker=series) \
        == pytest.approx(0.0175 * 0.25)


@pytest.mark.parametrize("series", MLB_ZERO_MAKER_SERIES)
def test_mlb_prop_series_are_maker_free(series):
    assert series_maker_charges_fee(series) is False
    assert fee_per_contract(0.5, maker=True, series_ticker=series) == 0.0
    # taker side is unaffected — 0.07*P*(1-P) regardless of series
    assert fee_per_contract(0.5, maker=False, series_ticker=series) \
        == pytest.approx(0.0175)


def test_mlb_prefix_collisions_resolve_by_longest_match():
    """KXMLB < KXMLBHR < KXMLBHRDERBY, and the answer alternates at each step.

    This is the case naive `startswith` matching gets wrong.
    """
    assert series_maker_charges_fee("KXMLB") is True          # league outcome
    assert series_maker_charges_fee("KXMLBHR") is False       # HR prop
    assert series_maker_charges_fee("KXMLBHRR") is False      # HR-allowed prop
    assert series_maker_charges_fee("KXMLBHRDERBY") is True   # derby outcome
    # KXMLBGAME must not be dragged to False by any prop family
    assert series_maker_charges_fee("KXMLBGAME") is True
    # KXMLBTOTAL and KXMLBTEAMTOTAL are distinct families, both maker-free
    assert series_maker_charges_fee("KXMLBTOTAL") is False
    assert series_maker_charges_fee("KXMLBTEAMTOTAL") is False


def test_full_market_series_tickers_resolve_to_their_family():
    # real tickers carry a suffix; prefix match must still land on the family
    assert series_maker_charges_fee("KXMLBHIT-25JUL20") is False
    assert series_maker_charges_fee("KXPGATOP20") is False
    assert series_maker_charges_fee("KXPGATOUR") is True


def test_unknown_series_defaults_to_charging():
    # conservative: over-charging only makes the net-edge gate stricter
    assert series_maker_charges_fee("KXNFLGAME") is True
    assert series_maker_charges_fee("TOTALLY-MADE-UP") is True


def test_golf_classification_unchanged():
    # regression guard on the pre-existing golf behaviour
    assert series_maker_charges_fee("KXPGA") is True
    assert series_maker_charges_fee("KXTHEOPEN") is True
    assert series_maker_charges_fee("KXPGAMAKECUT") is False
    assert series_maker_charges_fee("KXPGA3BALL") is False
    assert series_maker_charges_fee("KXGOLFH2H") is False


def test_no_series_ticker_keeps_the_0175_maker_fallback():
    """P-016 (src/pods/live_maker_pod.py) calls fee_per_contract(price,
    maker=True) with NO series_ticker and depends on this fallback. It makes on
    KXMLBGAME, which genuinely charges, so the fallback is correct — and the
    pod is mid-gate, so changing it would contaminate the running sample.
    """
    assert fee_per_contract(0.5, maker=True) == pytest.approx(0.0175 * 0.25)
    assert fee_per_contract(0.5, maker=True, series_ticker="") \
        == pytest.approx(0.0175 * 0.25)
    # and it agrees with what P-016 actually trades
    assert fee_per_contract(0.5, maker=True) \
        == fee_per_contract(0.5, maker=True, series_ticker="KXMLBGAME")


def test_fee_fraction_and_net_edge_honour_zero_maker_series():
    assert fee_fraction_of_stake(0.5, maker=True, series_ticker="KXMLBTB") == 0.0
    assert fee_fraction_of_stake(0.5, maker=True, series_ticker="KXMLBGAME") \
        == pytest.approx(0.0175 * 0.5)
    # a prop maker edge that the old (over-charging) model would have shaved
    assert net_edge(0.53, 0.50, maker=True, series_ticker="KXMLBHIT") \
        == pytest.approx(0.03)
    assert net_edge(0.53, 0.50, maker=True, series_ticker="KXMLBGAME") \
        == pytest.approx(0.03 - 0.0175 * 0.25)


# ---------------------------------------------------------------------------
# Round-leader + stat-leader series (added 2026-07-26).
#
# Ground truth re-read live on 2026-07-26 from
#   GET https://api.elections.kalshi.com/trade-api/v2/series/?category=...
# swept across EVERY category (7,665 series): 88 tickers contain "LEAD" and
# ALL 88 are `fee_type=quadratic`. None charges a maker fee.
#
# Why this matters: P-022 (round-leader dead-heat fade) is measured against a
# backtest that assumed zero maker fee. Only KXPGAR{1,2,3}LEAD were in the
# table, so every other tour was billed a phantom 0.0175*P*(1-P) — a
# systematic drag on the non-PGA tours, which supplied most of Phase 2's
# tournaments. See golf_quirks_research/P022_DECISION_RULE.md §3.
# ---------------------------------------------------------------------------

# fee_type == "quadratic" (ZERO maker fee), verified live 2026-07-26
GOLF_ROUND_LEADER_SERIES = [
    "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
    "KXDPWORLDTOURR1LEAD", "KXDPWORLDTOURR2LEAD", "KXDPWORLDTOURR3LEAD",
    "KXLIVR1LEAD", "KXLIVR2LEAD", "KXLIVR3LEAD",
    "KXLPGAR1LEAD", "KXLPGAR2LEAD", "KXLPGAR3LEAD",
    "KXCHAMPTOURR1LEAD",
]

# Season stat-leader family — 39 live series on 2026-07-26, all `quadratic`.
STAT_LEADER_SERIES = [
    "KXLEADERMLBWINS", "KXLEADERMLBHR", "KXLEADERMLBERA", "KXLEADERMLBWAR",
    "KXLEADERMLBSTRIKEOUTS", "KXLEADERNBAPTS", "KXLEADERNBAAST",
    "KXLEADERNFLSACKS", "KXLEADERNFLPYDS", "KXLEADERWNBAREB",
    "KXLEADERUCLGOALS",
]


@pytest.mark.parametrize("series", GOLF_ROUND_LEADER_SERIES)
def test_golf_round_leader_series_are_maker_free(series):
    assert series_maker_charges_fee(series) is False
    assert fee_per_contract(0.5, maker=True, series_ticker=series) == 0.0
    # taker side is untouched by the series table
    assert fee_per_contract(0.5, maker=False, series_ticker=series) \
        == pytest.approx(0.0175)


@pytest.mark.parametrize("series", STAT_LEADER_SERIES)
def test_stat_leader_family_is_maker_free(series):
    """All resolve through the single "KXLEADER" family prefix."""
    assert series_maker_charges_fee(series) is False
    assert fee_per_contract(0.5, maker=True, series_ticker=series) == 0.0


def test_no_phantom_maker_fee_at_p022_working_price():
    """The exact defect P022_DECISION_RULE.md §3 flagged: 0.129c/ct at P=0.08.

    P-022 quotes cheap round-leader names (anchor in [0.03, 0.12]). Before the
    fix, every non-PGA tour was charged 0.0175*0.08*0.92 = $0.001288/ct that
    Kalshi does not charge, biasing the forward estimate down against a rule
    calibrated on a fee-free backtest.
    """
    phantom = 0.0175 * 0.08 * 0.92          # $0.001288 == 0.129c
    assert phantom == pytest.approx(0.001288)
    for series in GOLF_ROUND_LEADER_SERIES:
        assert fee_per_contract(0.08, maker=True, series_ticker=series) == 0.0
    # and the fade's net edge is now unshaved
    assert net_edge(0.06, 0.08, maker=True, series_ticker="KXLIVR1LEAD") \
        == pytest.approx(-0.02)


def test_round_leader_series_survive_longest_prefix_shadowing():
    """Each new entry sits under a SHORTER, charging entry and must win.

    KXCHAMPTOUR (True) < KXCHAMPTOURR1LEAD (False)
    KXMLBHRDERBY (True) < KXMLBHRDERBYR1LEAD (False)
    """
    assert series_maker_charges_fee("KXCHAMPTOUR") is True
    assert series_maker_charges_fee("KXCHAMPTOURR1LEAD") is False
    # the derby chain now alternates FOUR times
    assert series_maker_charges_fee("KXMLB") is True
    assert series_maker_charges_fee("KXMLBHR") is False
    assert series_maker_charges_fee("KXMLBHRDERBY") is True
    assert series_maker_charges_fee("KXMLBHRDERBYR1LEAD") is False


def test_new_entries_do_not_shadow_the_one_charging_golf_neighbour():
    """KXPGARYDER is the trap: a short "KXPGAR" prefix would swallow it.

    Live 2026-07-26, KXPGARYDER is the only golf series under "KXPGAR" that is
    `quadratic_with_maker_fees`. This is why the round-leader entries are full
    tickers rather than per-tour round prefixes.
    """
    assert series_maker_charges_fee("KXPGARYDER") is True
    assert series_maker_charges_fee("KXPGARYDER-25") is True
    # ...while the leader markets around it stay free
    assert series_maker_charges_fee("KXPGAR1LEAD") is False
    # unrelated families are unchanged
    assert series_maker_charges_fee("KXLIVTOUR") is True
    assert series_maker_charges_fee("KXLPGATOUR") is True


def test_leader_markets_resolve_from_full_market_tickers():
    """Real tickers carry an event suffix; the family must still resolve."""
    assert series_maker_charges_fee("KXLPGAR2LEAD-26EVIAN") is False
    assert series_maker_charges_fee("KXDPWORLDTOURR3LEAD-26DUBAI") is False
    assert series_maker_charges_fee("KXLEADERMLBWINS-26") is False
    assert fee_per_contract(0.08, maker=True,
                            series_ticker="KXLIVR1LEAD-26CHICAGO") == 0.0


def test_every_lead_entry_in_the_table_is_maker_free():
    """Structural guard on the verified invariant.

    All 88 "LEAD" tickers live across every category are `quadratic`. If a
    future edit adds a charging *LEAD entry, either Kalshi changed its fee
    schedule (re-verify and update this test) or the entry is a mistake.
    """
    charging = {k: v for k, v in _SERIES_MAKER_FEE.items()
                if "LEAD" in k and v is True}
    assert charging == {}, f"*LEAD series marked as charging makers: {charging}"


def test_checkpoint_script_assertion_set_is_covered():
    """scripts/p022_checkpoint.py --check-fees must print OK, not DRIFTED.

    Keep this list in sync with the tuple in that script.
    """
    checked = ("KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
               "KXDPWORLDTOURR1LEAD", "KXLIVR1LEAD",
               "KXLPGAR1LEAD", "KXCHAMPTOURR1LEAD")
    bad = [s for s in checked
           if fee_per_contract(0.08, maker=True, series_ticker=s) > 0]
    assert bad == []


# ── Round-based top-N and non-PGA make-cut (added 2026-07-26, P-023c / P-023) ──

@pytest.mark.parametrize("series", [
    "KXPGAR1TOP5", "KXPGAR1TOP10", "KXPGAR1TOP20",
    "KXPGAR2TOP5", "KXPGAR2TOP10",
    "KXPGAR3TOP5", "KXPGAR3TOP10",
    "KXLIVTOP5", "KXLIVTOP10",
    "KXDPWORLDTOURMAKECUT",
])
def test_round_topn_and_nonpga_makecut_are_maker_free(series):
    """All verified `fee_type=quadratic` against GET /series?category=Sports
    on 2026-07-26 (3,005 series swept).

    These fell through to the charging default for months. `KXPGATOP` does NOT
    cover `KXPGAR1TOP5` — their shared prefix is only "KXPGA", which charges —
    and `KXLIVTOUR` is not a prefix of `KXLIVTOP5`.
    """
    assert series_maker_charges_fee(series) is False
    assert fee_per_contract(0.20, maker=True, series_ticker=series) == 0.0


def test_round_topn_entries_do_not_shadow_the_charging_golf_neighbours():
    """The regression that makes short prefixes dangerous here.

    "KXPGAR" would swallow KXPGARYDER, which genuinely charges. The entries use
    "KXPGAR1TOP"/"KXPGAR2TOP"/"KXPGAR3TOP", so the Ryder Cup is untouched.
    """
    assert series_maker_charges_fee("KXPGARYDER") is True
    assert series_maker_charges_fee("KXPGA") is True
    assert series_maker_charges_fee("KXPGATOUR") is True
    assert series_maker_charges_fee("KXLIVTOUR") is True
