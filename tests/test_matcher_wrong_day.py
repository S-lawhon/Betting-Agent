"""
tests/test_matcher_wrong_day.py
───────────────────────────────
Regression tests for the P-001 matcher's **wrong-day** defect.

The defect: every game of an MLB series carries the same two team names, so all
of them score identically under fuzzy matching and the winner was whichever the
Odds API happened to return first. The pod priced Friday's game and traded
Saturday's market. Measured 2026-07-26 over the whole corpus: **628 of 734
settled MLB placements (85.6%) sat 17–43h from their own ticker's game**, and
their CLV was ~0 (+0.19¢/ct) against +7.65¢/ct for correctly-matched rows.

Two protections exist in `Legacy/Kalshi Arb Project/src/matcher.py` and both are
asserted here, because the corpus shows the failure is silent — a wrong-day
match produces a perfectly plausible trade:

  1. **Tie-break by start-time proximity**, so list order cannot decide.
  2. **A hard time window** (`ticker_time_window_minutes`, 720 = 12h) that
     rejects the match outright when no candidate is near the ticker's own
     encoded start.

Both were verified against the live droplet on 2026-07-28; these tests keep them
verified. See `research/REPORT_Gate_Throughput_2026-07.md` §2.1.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

_LEGACY = (Path(__file__).resolve().parent.parent
           / "Legacy" / "Kalshi Arb Project" / "src")
if _LEGACY.is_dir() and str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

matcher = pytest.importorskip(
    "matcher", reason="Legacy Kalshi Arb Project sources not present")

UTC = dt.timezone.utc

# Real ticker from the live log: Baltimore @ Detroit, 2026-07-28 18:40 ET.
TICKER = "KXMLBGAME-26JUL281840BALDET-DET"
TITLE = "Will the Detroit Tigers beat the Baltimore Orioles?"
TRUE_START = dt.datetime(2026, 7, 28, 22, 40, tzinfo=UTC)     # 18:40 ET


class FakeEvent:
    """Minimal stand-in for OddsEvent."""

    def __init__(self, commence, event_id):
        self.home_team = "Detroit Tigers"
        self.away_team = "Baltimore Orioles"
        self.commence_time = commence
        self.event_id = event_id
        self.sport_key = "baseball_mlb"
        self.bookmakers = []


def _market(**over):
    m = {
        "ticker": TICKER,
        "title": TITLE,
        # What an OPEN KXMLBGAME market really carries: a placeholder close
        # days past its own first pitch. If the ticker ever stops parsing, this
        # is what the matcher falls back to — and it is worthless for timing.
        "close_time": "2026-07-31T23:20:00Z",
    }
    m.update(over)
    return m


def _matcher():
    return matcher.Matcher.from_config({})


RIGHT = FakeEvent(TRUE_START, "RIGHT")
DAY_BEFORE = FakeEvent(TRUE_START - dt.timedelta(days=1), "DAY_BEFORE")
DAY_AFTER = FakeEvent(TRUE_START + dt.timedelta(days=1), "DAY_AFTER")


def test_ticker_start_time_is_parsed_as_eastern():
    """18:40 ET on 2026-07-28 is 22:40 UTC. Reading the ticker as UTC shifts
    every MLB match by four hours and was how an earlier analysis produced a
    spurious 0.3% admissibility."""
    assert matcher._ticker_start_time(TICKER) == TRUE_START


@pytest.mark.parametrize("order", [
    [DAY_BEFORE, RIGHT, DAY_AFTER],
    [RIGHT, DAY_BEFORE, DAY_AFTER],
    [DAY_AFTER, DAY_BEFORE, RIGHT],
])
def test_same_matchup_on_three_days_resolves_to_the_ticker_day(order):
    """The core defect. All three score identically on names; only the start
    time distinguishes them, and list order must not decide."""
    r = _matcher().match(_market(), order, sport="baseball_mlb")
    assert r is not None
    assert r.odds_event.event_id == "RIGHT"
    delta_h = abs((r.odds_event.commence_time - TRUE_START).total_seconds()) / 3600
    assert delta_h == 0


def test_a_wrong_day_only_candidate_set_is_REJECTED_not_matched():
    """The protection that matters most.

    When the right day is simply absent from the odds feed, the matcher must
    return None rather than take the nearest same-name game. Silently trading
    the wrong day is what produced 628 bad placements.
    """
    assert _matcher().match(_market(), [DAY_BEFORE], sport="baseball_mlb") is None
    assert _matcher().match(_market(), [DAY_AFTER], sport="baseball_mlb") is None


def test_the_time_window_is_tight_enough_to_exclude_an_adjacent_day():
    """A 24h error must exceed the window. If `ticker_time_window_minutes` is
    ever raised past 1440, the wrong-day defect returns silently."""
    m = _matcher()
    assert m._ticker_window_minutes < 24 * 60
    assert m._ticker_window_minutes == 720.0


def test_a_genuinely_close_match_still_passes():
    """Guard against over-tightening: real Odds API commence times differ from
    the ticker by ~1 minute (the live 2026-07-27 placement was 0.02h out)."""
    near = FakeEvent(TRUE_START + dt.timedelta(minutes=1), "NEAR")
    r = _matcher().match(_market(), [near], sport="baseball_mlb")
    assert r is not None and r.odds_event.event_id == "NEAR"


def test_a_doubleheader_resolves_to_the_nearer_game():
    """Two games, same teams, same day, hours apart — the tie-break has to work
    at sub-day resolution too, not just across days."""
    game1 = FakeEvent(TRUE_START - dt.timedelta(hours=4), "GAME1")
    game2 = FakeEvent(TRUE_START, "GAME2")
    r = _matcher().match(_market(), [game1, game2], sport="baseball_mlb")
    assert r is not None and r.odds_event.event_id == "GAME2"


def test_falling_back_to_close_time_cannot_smuggle_in_a_wrong_day():
    """If the ticker carries no time the matcher falls back to `close_time` —
    which on an OPEN KXMLBGAME market is a placeholder days out (see CLAUDE.md).
    The fallback window is 30 days, so this path CANNOT distinguish days. The
    protection is that it is only reachable for tickers with no encoded time;
    this test documents that boundary rather than asserting it is safe.
    """
    untimed = _market(ticker="KXMLBGAME-BALDET-DET")
    assert matcher._ticker_start_time(untimed["ticker"]) is None
    m = _matcher()
    assert m._window_minutes >= 24 * 60, (
        "the close_time fallback window is deliberately loose; a ticker with no "
        "encoded start therefore has NO day-level protection")


# ── the standing instrument ──────────────────────────────────────────

def test_placement_admissibility_separates_matcher_generations():
    """The measurement that was missing for months.

    Rolling windows mix pre- and post-fix placements and will read low until
    the old rows age out, so a rate computed over them is not evidence about
    the matcher. `since_matcher_fix` is the number to judge it by.
    """
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "p001cp", root / "scripts" / "p001_checkpoint.py")
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)

    before = cp.MATCHER_FIX_EPOCH - dt.timedelta(days=1)
    after = cp.MATCHER_FIX_EPOCH + dt.timedelta(hours=1)
    wrong = TRUE_START - dt.timedelta(days=1)      # 24h out -> inadmissible
    placements = {
        "a": {"market_id": TICKER, "game_time": wrong, "placed_at": before},
        "b": {"market_id": TICKER, "game_time": wrong, "placed_at": before},
        "c": {"market_id": TICKER, "game_time": TRUE_START, "placed_at": after},
    }
    r = cp.placement_admissibility(placements)
    assert r["all_time"] == {"n": 3, "admissible": 1}
    assert r["since_matcher_fix"]["n"] == 1
    assert r["since_matcher_fix"]["rate"] == 1.0
    assert r["since_matcher_fix"]["max_delta_h"] == 0.0


def test_non_mlb_placements_are_not_scored():
    """Scenario D is MLB-only; tennis tickers encode a different scheme and
    must not dilute the measurement."""
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "p001cp2", root / "scripts" / "p001_checkpoint.py")
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    r = cp.placement_admissibility({
        "t": {"market_id": "KXATPMATCH-26JUL27ARNMUS-MUS",
              "game_time": TRUE_START,
              "placed_at": cp.MATCHER_FIX_EPOCH + dt.timedelta(hours=1)}})
    assert r["all_time"]["n"] == 0
