"""
tests/test_datagolf.py
──────────────────────
DataGolf client parsing + name matching, and P-017's use of a model
fair_prob. No network: a stub client returns canned probabilities.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.datagolf_client import DataGolfClient, player_from_title, _to_prob
from src.pods.golf_topn_pod import GolfTopNPod


# ── Client parsing / matching ─────────────────────────────────────────────

def test_player_from_title():
    assert player_from_title(
        "The Open: Will Scottie Scheffler finish top 20?") == "Scottie Scheffler"
    assert player_from_title(
        "PGA: Will Rory McIlroy make the cut?") == "Rory McIlroy"


def test_to_prob_normalizes_percent():
    assert _to_prob(0.45) == pytest.approx(0.45)
    assert _to_prob(45.0) == pytest.approx(0.45)   # percent form
    assert _to_prob("95") == pytest.approx(0.95)


def test_parse_and_fuzzy_match():
    data = {"baseline": [
        {"player_name": "Scheffler, Scottie", "dg_id": 1,
         "win": 18.0, "top_5": 42.0, "top_10": 58.0, "top_20": 75.0,
         "make_cut": 95.0},
        {"player_name": "McIlroy, Rory", "dg_id": 2,
         "top_20": 60.0, "make_cut": 90.0},
    ]}
    dg = DataGolfClient(api_key="x")
    dg._cache["pre"] = (dg._now(), DataGolfClient._parse(data))
    # exact-ish (name normalized "First Last") + series → field
    p = dg.prob_for("The Open: Will Scottie Scheffler finish top 20?",
                    "KXPGATOP20-THOC26-SCHE")
    assert p == pytest.approx(0.75)
    # make-cut field
    p2 = dg.prob_for("The Open: Will Rory McIlroy make the cut?",
                     "KXPGAMAKECUT-THOC26-MCIL")
    assert p2 == pytest.approx(0.90)
    # unknown player → None
    assert dg.prob_for("The Open: Will Nobody Here finish top 20?",
                       "KXPGATOP20-THOC26-XXXX") is None


def test_unavailable_without_key():
    dg = DataGolfClient(api_key="")
    assert dg.available is False
    assert dg.prob_for("Will X Y finish top 20?", "KXPGATOP20-E-XY") is None


# ── Pod uses model fair_prob when available ───────────────────────────────

class _StubKalshi:
    def __init__(self, markets):
        self._m = markets

    def open_markets(self, series_ticker, max_pages=10):
        return [m for m in self._m if m["ticker"].startswith(series_ticker)]


class _StubDG:
    def __init__(self, prob):
        self._p = prob

    def prob_for(self, title, series_ticker, kind="pre"):
        return self._p


def _market(ticker, event, yes_bid, yes_ask, close_dt, title):
    return {"ticker": ticker, "event_ticker": event, "title": title,
            "yes_bid_dollars": f"{yes_bid:.4f}", "yes_ask_dollars": f"{yes_ask:.4f}",
            "yes_ask_size_fp": "1000",
            "close_time": close_dt.isoformat().replace("+00:00", "Z")}


def test_pod_uses_model_prob_when_matched(tmp_path):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close,
                "XYZ: Will Scottie Scheffler finish top 20?")
    # model says 0.40 → blended with mid 0.19 at weight 0.5 = 0.295 → strong edge
    pod = GolfTopNPod(
        kalshi_public=_StubKalshi([m]), risk_manager=None,
        trade_log_path=tmp_path / "p.jsonl", _now_fn=lambda: now,
        datagolf_client=_StubDG(0.40), model_weight=0.5,
        min_net_edge_model=0.03)
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]
    assert len(placed) == 1
    r = placed[0]
    assert r.extra["fair_source"] == "datagolf"
    assert r.fair_prob == pytest.approx(0.5 * 0.40 + 0.5 * 0.19, abs=1e-6)


def test_pod_falls_back_to_structural_when_no_match(tmp_path):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-SCHE", "KXPGATOP20-XYZ26", 0.18, 0.20, close,
                "XYZ: Will Scottie Scheffler finish top 20?")
    pod = GolfTopNPod(
        kalshi_public=_StubKalshi([m]), risk_manager=None,
        trade_log_path=tmp_path / "p.jsonl", _now_fn=lambda: now,
        datagolf_client=_StubDG(None))   # model returns no match
    placed = [r for r in pod.scan_once() if r.action == "PLACED"]
    assert len(placed) == 1
    assert placed[0].extra["fair_source"] == "structural"
    assert placed[0].fair_prob == pytest.approx(0.24, abs=1e-6)  # ask + 0.04


def test_model_low_prob_skips(tmp_path):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(days=6)
    m = _market("KXPGATOP20-XYZ26-DOG", "KXPGATOP20-XYZ26", 0.18, 0.20, close,
                "XYZ: Will Some Longshot finish top 20?")
    # model says 0.10 (below price) → blended 0.145 < ask → negative edge → skip
    pod = GolfTopNPod(
        kalshi_public=_StubKalshi([m]), risk_manager=None,
        trade_log_path=tmp_path / "p.jsonl", _now_fn=lambda: now,
        datagolf_client=_StubDG(0.10), model_weight=0.5)
    assert [r for r in pod.scan_once() if r.action == "PLACED"] == []
