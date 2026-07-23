"""
tests/test_maker_diagnostics.py
───────────────────────────────
Guards the load-bearing logic added to scripts/maker_diagnostics.py:
markout-record routing, series-aware net markout, and the event-
clustered bootstrap.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

diag = importlib.import_module("scripts.maker_diagnostics")


def test_index_routes_next_event_separately():
    recs = [
        {"type": "FILL", "fill_id": "a", "price": 0.5, "ticker": "KXMLBGAME-X"},
        {"type": "MARKOUT", "fill_id": "a", "horizon_type": "wallclock",
         "horizon_s": 300.0, "markout_per_contract": 0.02},
        {"type": "MARKOUT", "fill_id": "a", "horizon_type": "next_event",
         "horizon_s": None, "markout_per_contract": -0.03},
        # legacy record with no horizon_type → treated as wall-clock
        {"type": "MARKOUT", "fill_id": "a", "horizon_s": 60.0,
         "markout_per_contract": 0.01},
        {"type": "SETTLE", "fill_id": "a", "result": 1.0},
    ]
    fills, markouts, settles, next_event = diag.index_fills(recs)
    assert set(markouts["a"]) == {300.0, 60.0}      # wall-clock only
    assert "a" in next_event
    assert next_event["a"]["markout_per_contract"] == -0.03
    assert "a" in settles


def test_net_markout_series_aware():
    # golf prop series charges ZERO maker fee → net == raw
    fills = {"g": {"fill_id": "g", "price": 0.2,
                   "ticker": "KXPGATOP10-OPEN-SMITH"}}
    markouts = {"g": {300.0: {"markout_per_contract": 0.03}}}
    assert abs(diag.net_markout(fills, markouts, "g", 300.0) - 0.03) < 1e-12

    # MLB game-winner series charges the general maker rate → net < raw
    fills = {"m": {"fill_id": "m", "price": 0.5, "ticker": "KXMLBGAME-X-HOU"}}
    markouts = {"m": {300.0: {"markout_per_contract": 0.03}}}
    net = diag.net_markout(fills, markouts, "m", 300.0)
    assert net < 0.03 and abs(net - (0.03 - 0.0175 * 0.25)) < 1e-9


def test_clustered_ci_point_matches_weighted_mean():
    # four clusters (>=3 → bootstrap runs), all value 0.02 → mean 0.02
    items = [{"c": i // 5, "v": 0.02, "w": 10} for i in range(20)]
    point, lo, hi = diag.clustered_ci(
        items, lambda f: f["v"], lambda f: f["w"], lambda f: f["c"],
        n_boot=500)
    assert abs(point - 0.02) < 1e-12
    # no spread across clusters → CI collapses on the point
    assert lo is not None and abs(lo - 0.02) < 1e-9 and abs(hi - 0.02) < 1e-9


def test_clustered_ci_too_few_clusters():
    items = [{"c": 0, "v": 0.01, "w": 1}, {"c": 1, "v": 0.03, "w": 1}]
    point, lo, hi = diag.clustered_ci(
        items, lambda f: f["v"], lambda f: f["w"], lambda f: f["c"])
    assert point is not None and lo is None and hi is None  # <3 clusters


def test_series_of_and_cluster_of():
    assert diag.series_of({"ticker": "KXMLBGAME-26JUL20-HOU"}) == "KXMLBGAME"
    assert diag.series_of({}) == ""
    cfg = diag.POD_CONFIG["P-016"]
    assert diag.cluster_of({"game_pk": 42, "event": "x"}, cfg) == 42
    cfg = diag.POD_CONFIG["P-017M"]
    assert diag.cluster_of({"event": "OPEN0"}, cfg) == "OPEN0"
