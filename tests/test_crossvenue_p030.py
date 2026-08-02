"""P-030 cross-venue (Kalshi <-> ProphetX) test suite.

These tests pin the facts that the P-030 research verdict rests on. Several of
them exist specifically to fail loudly if a "harmless refactor" moves a number
that a pre-registered gate depends on.
"""
from __future__ import annotations

import importlib
import inspect
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import prophetx_fees as F           # noqa: E402
from src import prophetx_ladder as L         # noqa: E402


# ── ladder ───────────────────────────────────────────────────────────────────

def test_ladder_shape():
    assert len(L.VALID_ODDS) == 291
    assert L.VALID_ODDS == sorted(set(L.VALID_ODDS))
    assert L.VALID_ODDS[0] == -10000 and L.VALID_ODDS[-1] == 10000


def test_ladder_is_finer_than_kalshis_cent_grid_everywhere():
    """The finding that kept the thesis alive: if ProphetX's grid were coarser
    than 1c anywhere, a 1-cent-precision lock would not be expressible there."""
    probs = sorted(L.american_to_prob(o) for o in L.VALID_ODDS)
    gaps = [(probs[i + 1] - probs[i]) * 100 for i in range(len(probs) - 1)]
    assert max(gaps) < 1.0, f"coarsest tick {max(gaps):.4f}pp exceeds Kalshi's 1c"
    assert round(min(gaps), 4) == 0.1684
    assert round(max(gaps), 4) == 0.7576


def test_ladder_probability_range():
    probs = [L.american_to_prob(o) for o in L.VALID_ODDS]
    assert round(min(probs) * 100, 4) == 0.9901
    assert round(max(probs) * 100, 4) == 99.0099


@pytest.mark.parametrize("odds,prob", [
    (100, 0.5), (-101, 0.502488), (101, 0.497512), (-110, 0.523810),
    (-200, 2 / 3), (200, 1 / 3),
])
def test_american_to_prob(odds, prob):
    assert L.american_to_prob(odds) == pytest.approx(prob, abs=1e-6)


def test_no_gap_at_even_money():
    """Ladder runs ... -102, -101, +100, +101 ... continuously. +100 is the
    50% tick; -100 is absent, so snap_prob(0.5) must return +100, not fail."""
    near = [o for o in L.VALID_ODDS if -103 <= o <= 103]
    assert near == [-103, -102, -101, 100, 101, 102, 103]
    assert L.snap_prob(0.5) == (100, 0.5)


def test_roundtrip_prob_to_american():
    for o in L.VALID_ODDS:
        assert L.prob_to_american(L.american_to_prob(o)) == pytest.approx(o, rel=1e-9)


def test_snap_direction_is_never_generous():
    """'down' must never return a price above the target and 'up' never below.
    Snapping to 'nearest' when buying would manufacture up to half a tick
    (~0.38pp) of edge that is not orderable."""
    for target in (0.07, 0.2333, 0.5, 0.5001, 0.734, 0.91):
        assert L.snap_prob(target, direction="down")[1] <= target + 1e-12
        assert L.snap_prob(target, direction="up")[1] >= target - 1e-12
        near = L.snap_prob(target, direction="nearest")[1]
        assert (L.snap_prob(target, direction="down")[1] <= near
                <= L.snap_prob(target, direction="up")[1])


def test_snap_clamps_outside_ladder():
    assert L.snap_prob(0.0001)[1] == pytest.approx(0.009901, abs=1e-6)
    assert L.snap_prob(0.9999)[1] == pytest.approx(0.990099, abs=1e-6)


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        L.american_to_prob(0)
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            L.prob_to_american(bad)
    with pytest.raises(ValueError):
        L.snap_prob(0.5, direction="sideways")


# ── fees ─────────────────────────────────────────────────────────────────────

def test_prophetx_fee_is_paid_only_by_the_winner():
    """The structural difference from Kalshi. A loser costs zero -- code that
    debits it per-contract regardless would overstate cost on every losing leg."""
    assert F.realized_fee_per_contract(0.485, won=False) == 0.0
    assert F.realized_fee_per_contract(0.485, won=True) == pytest.approx(
        0.02 * 0.515)


def test_prophetx_taker_is_exactly_3_5x_cheaper_than_kalshi_at_every_price():
    """0.07 / 0.02 = 3.5, price-independent. This is the routing rule."""
    for p in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
        k = F.kalshi_fee_per_contract(p)
        x = F.expected_fee_per_contract(p)
        assert k / x == pytest.approx(3.5, rel=1e-12)


def test_expected_fee_uses_price_as_probability_by_default():
    assert F.expected_fee_per_contract(0.4) == pytest.approx(0.4 * 0.02 * 0.6)
    assert F.expected_fee_per_contract(0.4, win_prob=0.5) == pytest.approx(
        0.5 * 0.02 * 0.6)


def test_combined_corridor_is_nine_percent_parabola():
    for p in (0.2, 0.5, 0.8):
        assert F.breakeven_gap(p) == pytest.approx(0.09 * p * (1 - p))
    assert F.breakeven_gap(0.5) * 100 == pytest.approx(2.25)


def test_kalshi_maker_free_series_collapse_the_corridor():
    assert F.breakeven_gap(0.5, kalshi_maker=True,
                           series_charges_maker=False) * 100 == pytest.approx(0.50)
    assert F.breakeven_gap(0.5, kalshi_maker=True,
                           series_charges_maker=True) * 100 == pytest.approx(0.94,
                                                                             abs=0.01)


def test_phantom_tax_is_zero_under_capital_characterization():
    """Gate 0. Capital/s1256 treatment nets offsetting positions, so the term
    vanishes; gambling treatment does not."""
    assert F.phantom_tax_per_pair(0.5, 0.03, tax_rate=0.0) == 0.0
    assert F.breakeven_gap(0.5, tax_rate=0.0) == pytest.approx(0.0225)
    assert F.phantom_tax_per_pair(0.5, 0.03, tax_rate=0.32) > 0


def test_after_tax_breakeven_matches_the_published_report():
    """3.79c exact vs the 3.85c approximation quoted in the spec. The gate uses
    the larger (stricter) number on purpose -- assert both so a change to this
    function cannot silently loosen the pre-registered threshold."""
    assert F.breakeven_gap(0.5, tax_rate=0.32) * 100 == pytest.approx(3.79, abs=0.01)
    approx = (0.09 + 0.20 * 0.32) * 0.25 * 100
    assert approx == pytest.approx(3.85, abs=0.01)
    assert F.breakeven_gap(0.5, tax_rate=0.32) * 100 < approx


def test_expected_loss_per_pair_closed_form():
    """E[loss] = 2P(1-P) - gap/2, derived from a = P-g/2, b = (1-P)-g/2."""
    for p, g in ((0.5, 0.03), (0.9, 0.02), (0.3, 0.05)):
        a, b = p - g / 2, (1 - p) - g / 2
        assert F.expected_loss_per_pair(p, g) == pytest.approx(p * b + (1 - p) * a)


def test_phantom_tax_hits_arb_far_harder_than_a_directional_maker():
    """The core argument of the R6 report: the tax scales with gross turnover,
    the edge scales with the gap. ~20x+ at a 3c gap."""
    arb_edge = 0.03 - F.breakeven_gap(0.5)
    arb_tax = F.phantom_tax_per_pair(0.5, 0.03, 0.32)
    # P-022: sell YES at 7c, wins 96.5% -> E[loss] = 0.035 * 0.93
    p022_tax = (1 - F.DEDUCTIBLE_LOSS_FRAC) * (0.035 * 0.93) * 0.32
    assert (arb_tax / arb_edge) > 2.0            # >200% of edge
    assert (p022_tax / 0.035) < 0.15             # <15% of edge
    assert (arb_tax / arb_edge) / (p022_tax / 0.035) > 20


def test_rule46_ceiling_is_multiplicative_not_percentage_points():
    """15% OF PRICE. The misreading (15 percentage points) would say the tails
    are safe; they are the most exposed part of the book."""
    assert F.no_cancel_ceiling(0.50) == pytest.approx(0.075)
    assert F.no_cancel_ceiling(0.10) == pytest.approx(0.015)
    assert F.no_cancel_ceiling(0.10) < 0.15


def test_corridor_closes_in_the_tails_and_is_open_mid_price():
    """The inversion: fees point at the tails, Rule 4.6 closes them."""
    rows = {round(r[0], 2): r for r in F.corridor_table(tax_rate=0.32)}
    assert rows[0.50][4] > 3.0          # window wide at even money
    assert rows[0.90][4] < 0.25         # effectively closed
    assert rows[0.95][4] < 0.10
    windows = [rows[p][4] for p in (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)]
    assert windows == sorted(windows, reverse=True)   # monotone shrinking


def test_rebate_is_marginal_and_not_applied_by_default():
    assert F.rebate_rate(0) == 0.0
    assert F.rebate_rate(50_000) == 0.0
    assert F.rebate_rate(7_500_000) == pytest.approx(0.4987, abs=1e-4)
    assert F.rebate_rate(10_000_000) > F.rebate_rate(7_500_000)
    # default fee paths must ignore it entirely
    assert F.expected_fee_per_contract(0.5) == pytest.approx(0.005)


# ── read-only guarantee ──────────────────────────────────────────────────────

BANNED = ("submit_order", "cancel_order", "place_order", "cancel_all_orders",
          "cancel_orders_by_event", "cancel_orders_by_market")


def _executable_names(path: str) -> set:
    """Identifiers that actually appear in CODE, with docstrings and comments
    excluded. A plain substring scan is useless here -- both modules discuss
    the order endpoints at length in prose precisely to explain why they are
    absent, and that prose must not trip the tripwire."""
    import ast
    tree = ast.parse(open(path).read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # string literals ARE live (URL paths), but a docstring is the
            # first statement of a module/def and is not.
            names.add(node.value)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                names.discard(doc)
    return names


def test_prophetx_client_has_no_order_path():
    """P-030 is NOT green-lit. Measurement only. If someone adds an order path
    to this client, this test is the tripwire."""
    mod = importlib.import_module("src.prophetx_client")
    names = _executable_names(inspect.getfile(mod))
    for banned in BANNED:
        assert not any(banned in n for n in names), \
            f"order path leaked into client: {banned}"
    assert not any(s for s in names
                   if isinstance(s, str) and "submit" in s and s.startswith("/"))
    public = [n for n in dir(mod.ProphetXClient) if not n.startswith("_")]
    assert not any("order" in n or "cancel" in n for n in public), public


def test_collector_never_imports_an_order_path():
    names = _executable_names(os.path.join(ROOT, "scripts",
                                           "collect_crossvenue_basis.py"))
    for banned in BANNED:
        assert not any(banned in n for n in names), banned


def test_client_without_credentials_raises_rather_than_returning_empty():
    """An empty sample must never be mistakable for 'no basis found'."""
    from src.prophetx_client import ProphetXClient, ProphetXAuthError
    c = ProphetXClient(access_key="", secret_key="", env="sandbox")
    assert c.has_credentials is False
    with pytest.raises(ProphetXAuthError):
        c.get("/v4/mm/get_sport_events")
    info = c.probe()                       # probe must never raise
    assert info["has_credentials"] is False and info["error"]
    # ladder still available offline, from the pinned constant
    assert len(c.price_ladder()) == 291


def test_client_rejects_unknown_env():
    from src.prophetx_client import ProphetXClient
    with pytest.raises(ValueError):
        ProphetXClient(env="prod")


def test_client_uses_current_unversioned_market_data_routes(monkeypatch):
    from src.prophetx_client import ProphetXClient
    c = ProphetXClient(access_key="access", secret_key="secret", env="sandbox")
    calls = []

    def fake_get(path, params=None, retries=3):
        calls.append((path, params))
        return {"data": {"tournaments": [], "sport_events": [], "markets": []}}

    monkeypatch.setattr(c, "get", fake_get)
    c.tournaments()
    c.sport_events("109")
    c.markets_for_event("123")
    assert calls == [
        ("/mm/get_tournaments", None),
        ("/mm/get_sport_events", {"tournament_id": "109"}),
        ("/mm/get_markets", {"event_id": "123"}),
    ]


def test_moneyline_book_parses_current_nested_sandbox_shape():
    from src.prophetx_client import ProphetXClient
    c = ProphetXClient(access_key="access", secret_key="secret", env="sandbox")
    market = {"type": "moneyline", "selections": [
        [{"name": "Los Angeles Dodgers", "odds": -160, "stake": 25,
          "value": 38.99}],
        [{"name": "Boston Red Sox", "odds": 154, "stake": 40,
          "value": 25.97}],
    ]}
    book = c.moneyline_book(market)
    dodgers, red_sox = book["Los Angeles Dodgers"], book["Boston Red Sox"]
    assert book["_unparsed"] == []
    assert dodgers["ask_odds"] == -160
    assert dodgers["ask_prob"] == pytest.approx(160 / 260)
    assert dodgers["ask_size"] == 25
    assert red_sox["ask_odds"] == 154
    assert red_sox["bid_prob"] == pytest.approx(1 - dodgers["ask_prob"])
    assert red_sox["bid_size"] == 25
    assert red_sox["bid_derived_from_complement"] is True


def test_shape_dump_waits_for_a_populated_market(tmp_path):
    from src.prophetx_client import ProphetXClient
    path = tmp_path / "shapes.jsonl"
    c = ProphetXClient(access_key="access", secret_key="secret", env="sandbox",
                       dump_shapes_path=str(path))
    c._record_shape("/mm/get_markets", {"data": {"markets": None}})
    assert not path.exists()
    c._record_shape("/mm/get_markets", {
        "data": {"markets": [{"type": "moneyline", "selections": []}]}})
    recorded = path.read_text()
    assert '"markets": [' in recorded
    assert '"selections"' in recorded


# ── collector logic ──────────────────────────────────────────────────────────

def test_collector_refuses_to_run_without_credentials(monkeypatch):
    """Exit code 2, no data directory created."""
    env = dict(os.environ)
    env.pop("PROPHETX_ACCESS_KEY", None)
    env.pop("PROPHETX_SECRET_KEY", None)
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts",
                                      "collect_crossvenue_basis.py"), "--probe"],
        capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr or "BLOCKED" in r.stdout


def test_universe_excludes_golf_and_props():
    import scripts.collect_crossvenue_basis as C
    assert set(C.SERIES) == {"KXMLBGAME", "KXNBAGAME", "KXNHLGAME",
                             "KXATPMATCH", "KXWTAMATCH"}
    assert not any("PGA" in s or "GOLF" in s or "LEAD" in s for s in C.SERIES)
    # every series in the universe charges Kalshi maker fees
    assert all(C.SERIES_CHARGES_MAKER.values())


def test_matcher_refuses_sports_with_no_vocab():
    """A wrong match manufactures a basis between two different events, which
    is worse than no match. Empty vocab must yield zero pairs, not a guess."""
    import scripts.collect_crossvenue_basis as C
    kg = {"KXNBAGAME-X": {"series": "KXNBAGAME", "sport": "nba",
                          "tickers": {"BOS": "t1", "LAL": "t2"}, "titles": {}}}
    pairs, reasons = C.match_pairs(kg, [])
    assert pairs == [] and reasons["no_vocab_for_sport"] == 1


def test_matcher_pairs_mlb_on_canonical_abbreviations():
    import scripts.collect_crossvenue_basis as C
    kg = {"KXMLBGAME-26JUL29NYYBOS": {
        "series": "KXMLBGAME", "sport": "mlb",
        "tickers": {"NYY": "tk-NYY", "BOS": "tk-BOS"}, "titles": {},
        "scheduled_start_at": "2026-07-29T23:05:00+00:00"}}
    px = [{"event_id": "e1", "title": "Yankees @ Red Sox",
           "names": ["New York Yankees", "Boston Red Sox"],
           "scheduled_start_at": "2026-07-29T23:05:00Z",
           "book": {"New York Yankees": {"ask_prob": 0.44},
                    "Boston Red Sox": {"ask_prob": 0.58}}}]
    pairs, _ = C.match_pairs(kg, px)
    assert len(pairs) == 1
    assert pairs[0]["px_names"] == {"NYY": "New York Yankees",
                                    "BOS": "Boston Red Sox"}


def test_matcher_rejects_same_teams_on_wrong_date():
    import scripts.collect_crossvenue_basis as C
    kg = {"game": {"series": "KXMLBGAME", "sport": "mlb",
                   "tickers": {"NYY": "a", "BOS": "b"}, "titles": {},
                   "scheduled_start_at": "2026-08-05T23:05:00Z"}}
    px = [{"event_id": "e1", "title": "Yankees @ Red Sox",
           "names": ["New York Yankees", "Boston Red Sox"],
           "scheduled_start_at": "2026-08-03T23:05:00Z", "book": {}}]
    pairs, reasons = C.match_pairs(kg, px)
    assert pairs == []
    assert reasons["schedule_mismatch"] == 1


def test_prophetx_universe_keeps_only_exact_main_game_moneyline():
    import scripts.collect_crossvenue_basis as C

    class FakePx:
        def sport_events(self):
            return [{"event_id": 1, "name": "Yankees at Red Sox",
                     "scheduled": "2026-08-03T23:05:00Z"}]

        def markets_for_event(self, event_id):
            return [{"id": 1, "type": "moneyline", "name": "Moneyline"},
                    {"id": 2, "type": "moneyline",
                     "name": "1st 5 Innings Moneyline"}]

        def moneyline_book(self, market):
            return {"_unparsed": [], "New York Yankees": {},
                    "Boston Red Sox": {}}

    rows = C.prophetx_moneylines(FakePx())
    assert len(rows) == 1
    assert rows[0]["market_id"] == 1


def test_gap_is_one_minus_both_asks():
    import scripts.collect_crossvenue_basis as C
    assert C._gap(0.48, 0.49) == pytest.approx(0.03)
    assert C._gap(0.55, 0.50) == pytest.approx(-0.05)     # no arb
    assert C._gap(None, 0.5) is None and C._gap(0.5, None) is None


def test_snapshot_row_carries_every_gate_input():
    import scripts.collect_crossvenue_basis as C

    class FakeK:
        def orderbook(self, tk):
            return {"yes_bid": 0.47, "yes_ask": 0.49,
                    "bid_qty": 800, "ask_qty": 900}

    pair = {"game_id": "G1", "series": "KXMLBGAME", "sport": "mlb",
            "px_environment": "sandbox",
            "kalshi_scheduled_start_at": "2026-08-03T23:05:00+00:00",
            "px_scheduled_start_at": "2026-08-03T23:10:00+00:00",
            "kalshi_tickers": {"NYY": "tk-NYY", "BOS": "tk-BOS"},
            "px_event_id": "e1", "px_names": {"NYY": "Yanks", "BOS": "Sox"},
            "px_book": {"Yanks": {"ask_prob": 0.50, "ask_size": 5000,
                                  "bid_prob": 0.48, "ask_odds": 100,
                                  "bid_odds": 104, "bid_size": 4000},
                        "Sox": {"ask_prob": 0.48, "ask_size": 6000,
                                "bid_prob": 0.46, "ask_odds": 108,
                                "bid_odds": 117, "bid_size": 3000}}}
    rows = C.snapshot(FakeK(), None, pair, tax_rate=0.32)
    assert len(rows) == 2
    assert all(row["px_environment"] == "sandbox" for row in rows)
    assert all(row["schedule_skew_seconds"] == 300 for row in rows)
    r = rows[0]
    for k in ("gap", "breakeven_fees_only", "breakeven_with_tax",
              "px_leg_edge_vs_fmv", "rule46_ceiling", "rule46_bustable",
              "in_spec_band", "px_other_ask_size", "k_ask_qty"):
        assert k in r, k
    # buy Kalshi NYY at 0.49, buy ProphetX BOS at 0.48 -> 3c lock
    assert r["gap"] == pytest.approx(0.03)
    assert r["in_spec_band"] is True                     # k_mid = 0.48
    assert r["breakeven_with_tax"] > r["breakeven_fees_only"]
    # 3c gap does NOT clear the after-tax floor -- the report's central point
    assert r["gap"] * 100 < r["breakeven_with_tax"] * 100


# ── analyzer ─────────────────────────────────────────────────────────────────

def test_analyzer_self_test_passes():
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts",
                                      "analyze_crossvenue_gates.py"),
         "--self-test"], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "self-test: OK" in r.stdout


def test_analyzer_treats_empty_sample_as_error_not_kill(tmp_path):
    """An empty directory must not read as evidence of no basis."""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts",
                                      "analyze_crossvenue_gates.py"),
         "--data-dir", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "not a kill" in (r.stdout + r.stderr).lower()


def test_gate_thresholds_match_the_spec():
    """Pre-registered constants. Changing one should require changing the spec,
    so this test is the thing that makes that visible in review."""
    import scripts.analyze_crossvenue_gates as A
    assert A.GATE1_MIN_DEPTH_USD == 2_000.0
    assert A.GATE2_MIN_GAP_CENTS == 3.85
    assert A.GATE2_MIN_FREQ == 0.02
    assert A.GATE2_BAND == (0.40, 0.65)
    assert A.GATE3_MIN_SURVIVAL_SECS == 30.0
    assert A.GATE4_MIN_DAILY_USD == 5_000.0


def test_gate2_only_counts_in_band_observations():
    import scripts.analyze_crossvenue_gates as A
    rows = ([{"game_id": f"g{i}", "team": "A", "in_spec_band": False,
              "gap": 0.10, "ts_utc": "2026-07-29T00:00:00"} for i in range(20)]
            + [{"game_id": f"h{i}", "team": "A", "in_spec_band": True,
                "gap": 0.001, "ts_utc": "2026-07-29T00:00:00"} for i in range(20)])
    g = A.gate2_gap(rows)
    assert g["value"] == 0.0 and g["pass"] is False      # 10c out-of-band ignored


def test_gate3_uses_observed_lower_bound_for_survival():
    """A single-snapshot gap scores 0s, not one sample interval -- we never
    credit survival time we did not observe."""
    import scripts.analyze_crossvenue_gates as A
    rows = [{"game_id": "g", "team": "A", "in_spec_band": True, "gap": 0.10,
             "ts_utc": "2026-07-29T00:00:00"},
            {"game_id": "g", "team": "A", "in_spec_band": True, "gap": 0.001,
             "ts_utc": "2026-07-29T00:01:00"}]
    assert A.gate3_persistence(rows)["value"] == 0.0


def test_verdict_is_kill_if_any_single_gate_fails():
    import scripts.analyze_crossvenue_gates as A
    rows = [{"game_id": f"g{i}", "team": "A", "in_spec_band": True,
             "gap": 0.10, "ts_utc": "2026-07-29T00:00:00",
             "px_other_ask_size": 10.0, "px_other_ask_prob": 0.5}
            for i in range(40)]
    res = A.run(rows)                        # depth = $5, gate 1 fails
    assert res["gates"][0]["pass"] is False
    assert res["verdict"] == "KILL"


def test_rule46_diagnostic_counts_bustable_qualifying_fills():
    import scripts.analyze_crossvenue_gates as A
    rows = [{"gap": 0.10, "rule46_bustable": True},
            {"gap": 0.10, "rule46_bustable": False},
            {"gap": 0.001, "rule46_bustable": True}]      # not qualifying
    d = A.bust_exposure(rows)
    assert d["qualifying"] == 2 and d["bustable"] == 1 and d["share"] == 0.5
