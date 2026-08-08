"""
tests/test_p014_checkpoint.py
─────────────────────────────
Tests for the P-014 gate reader (`scripts/p014_checkpoint.py`).

The rule is LOCKED (research/P014_DECISION_RULE.md, 2026-07-31, option (a)),
so unlike the pre-lock version of this file — which asserted the reader must
NEVER emit a verdict — these tests assert the reader implements exactly the
locked §4 schedule and the locked §3 clustering, and nothing else:

* the statistic is fee-net ¢/contract, contract-weighted WITHIN a game,
  EQUAL weight per game ACROSS games;
* n (progress) counts admissible settled rows — a row with no readable
  price/side/stake/event SHRINKS n, it is never defaulted;
* `venue_prob` is the YES price, so a NO position's entry is `1 − venue_prob`
  (verified against booked pnl_usd on the droplet, 2026-07-31);
* scalar results are partial payouts at settlement_value_dollars, never void;
* HARD KILL (z ≤ −2) fires at any n; NO DECISION below 500; at 500+:
  KILL if edge ≤ 0, PASS if z ≥ 2, else CONTINUE; at 900+ with z < 2: KILL.
"""
import gzip
import importlib.util
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SPEC = importlib.util.spec_from_file_location(
    "p014_checkpoint",
    Path(__file__).resolve().parent.parent / "scripts" / "p014_checkpoint.py")
cp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cp)


def _row(fp, outcome="WIN", venue_prob=0.5, pod="P-014", side="YES",
         stake=50.0, event=None, result=None, sv=None):
    """The exact shape P-014 writes: fill_price is NULL, entry is venue_prob
    (the YES price), stake in position_size_usd, game in `event`."""
    r = {
        "fingerprint": fp, "pod_id": pod,
        "market_id": f"KXMLBGAME-{fp}", "market_ticker": f"KXMLBGAME-{fp}",
        "outcome": outcome, "fill_price": None, "venue_prob": venue_prob,
        "kalshi_prob": venue_prob, "fair_prob": 0.343, "pnl_usd": 5.69,
        "side": side, "position_size_usd": stake,
        "event": event if event is not None else f"game-{fp}",
        "settled_at_utc": "2026-07-25T19:35:22.063209+00:00",
    }
    if result is not None:
        r["result"] = result
    if sv is not None:
        r["settlement_value_dollars"] = sv
    return r


def _write(path: Path, rows, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda: gzip.open(path, "wt")) if gz else (lambda: open(path, "w"))
    with opener() as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return str(path)


def _logs(tmp_path):
    return [str(tmp_path / p) for p in cp.DEFAULT_LOGS]


def _mix(n_win, n_loss):
    """n_win + n_loss rows at p=0.5, one game each: WIN → +48¢, LOSS → −52¢
    per contract fee-net (fee at 0.5 rounds up to 2¢)."""
    rows = [_row(f"w{i}", "WIN") for i in range(n_win)]
    rows += [_row(f"l{i}", "LOSS") for i in range(n_loss)]
    return rows


# ── the fill economics (§2, §4) ──────────────────────────────────────

def test_yes_fill_pnl_is_fee_net_at_the_traded_price():
    pr = cp.fill_pnl(_row("a", "WIN", venue_prob=0.5, stake=50.0))
    assert abs(pr["pnl_ct"] - 0.48) < 1e-12          # 1 − 0.5 − 0.02
    assert abs(pr["contracts"] - 100.0) < 1e-9       # 50 / 0.5
    pr = cp.fill_pnl(_row("b", "LOSS", venue_prob=0.5))
    assert abs(pr["pnl_ct"] - (-0.52)) < 1e-12       # 0 − 0.5 − 0.02


def test_no_side_prices_at_one_minus_venue_prob():
    """venue_prob is the YES price. A NO win at yes-price 0.505 costs 0.495
    and pays $1 — the convention verified against booked pnl_usd."""
    pr = cp.fill_pnl(_row("a", "WIN", venue_prob=0.505, side="NO", stake=71.43))
    assert abs(pr["pnl_ct"] - (1.0 - 0.495 - 0.02)) < 1e-12
    assert abs(pr["contracts"] - 71.43 / 0.495) < 1e-9


def test_scalar_is_a_partial_payout_never_void():
    """§2: result='scalar' settles at settlement_value_dollars (YES gets sv,
    NO gets 1−sv) and is never excluded."""
    pr = cp.fill_pnl(_row("a", "WIN", venue_prob=0.3, result="scalar", sv=0.4))
    assert abs(pr["pnl_ct"] - (0.4 - 0.3 - 0.02)) < 1e-12
    pr = cp.fill_pnl(_row("b", "LOSS", venue_prob=0.3, side="NO",
                          result="scalar", sv=0.4))
    assert abs(pr["pnl_ct"] - (0.6 - 0.7 - 0.02)) < 1e-12


def test_scalar_without_a_value_is_excluded_never_defaulted():
    assert cp.fill_pnl(_row("a", "WIN", result="scalar")) is None


def test_missing_side_or_stake_is_excluded_never_defaulted():
    bad = _row("a")
    bad["side"] = None
    assert cp.fill_pnl(bad) is None
    bad = _row("b")
    bad["position_size_usd"] = None
    assert cp.fill_pnl(bad) is None


# ── clustering (§3) ──────────────────────────────────────────────────

def test_games_enter_with_equal_weight():
    """One game with 4 winning fills counts the same as a game with 1 losing
    fill — the P-017M phantom-edge lesson, hard-coded."""
    rows = [_row(f"a{i}", "WIN", event="G1") for i in range(4)]
    rows += [_row("b", "LOSS", event="G2"), _row("c", "LOSS", event="G3")]
    s = cp.cluster_by_game(rows)
    assert s["games"] == 3
    # mean(+48, −52, −52), NOT the row-mean (which would be +14.67)
    assert abs(s["edge_cents_per_contract"] - (48 - 52 - 52) / 3) < 1e-9


def test_within_game_mean_is_contract_weighted():
    rows = [_row("a", "WIN", stake=50.0, event="G1"),     # 100 cts, +0.48
            _row("b", "LOSS", stake=25.0, event="G1")]    # 50 cts, −0.52
    s = cp.cluster_by_game(rows)
    assert s["games"] == 1
    expect = 100.0 * (0.48 * 100 - 0.52 * 50) / 150
    assert abs(s["edge_cents_per_contract"] - expect) < 1e-9


def test_unpriced_rows_shrink_n(tmp_path):
    """§2: n_unpriced must be reported and n SHRUNK, never papered over."""
    bad = _row("b")
    for k in ("fill_price", "venue_prob", "kalshi_prob"):
        bad[k] = None
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), bad])
    r = cp.evaluate(cp.load_terminal(_logs(tmp_path)))
    assert r["progress"] == 1 and r["n_unpriced"] == 1


# ── the decision schedule (§4, §7) ───────────────────────────────────

def test_no_decision_below_500():
    r = cp.evaluate(_mix(5, 5))
    assert r["verdict"] == "NO DECISION"
    assert "underpowered" in r["reason"]


def test_hard_kill_fires_at_any_n():
    r = cp.evaluate(_mix(5, 45))          # edge −42¢, z ≈ −9.9 at n=50
    assert r["verdict"] == "HARD KILL"
    assert r["z"] <= -2.0


def test_kill_at_500_when_edge_not_positive():
    r = cp.evaluate(_mix(250, 250))       # edge = −2¢ at n=500
    assert r["progress"] == 500
    assert r["verdict"] == "KILL"
    assert r["edge_cents_per_contract"] <= 0


def test_pass_at_500_needs_z_at_least_2():
    r = cp.evaluate(_mix(400, 100))       # edge +28¢, z ≈ 15.7
    assert r["verdict"] == "PASS"
    assert "NOT live money" in r["reason"]


def test_continue_is_the_single_extension():
    r = cp.evaluate(_mix(270, 230))       # edge +2¢, z ≈ 0.9
    assert r["verdict"] == "CONTINUE"
    assert "P-014b" in r["reason"]


def test_kill_at_900_when_the_extension_is_spent():
    r = cp.evaluate(_mix(480, 430))       # n=910, edge +0.75¢, z ≈ 0.45
    assert r["progress"] >= cp.EXTENSION_N
    assert r["verdict"] == "KILL"
    assert "extension" in r["reason"]


def test_verdicts_match_a_hand_computed_statistic():
    """The z the reader prints must be the §4 formula, not an approximation."""
    r = cp.evaluate(_mix(270, 230))
    xs = [48.0] * 270 + [-52.0] * 230
    mean = sum(xs) / len(xs)
    sd = math.sqrt(sum((v - mean) ** 2 for v in xs) / (len(xs) - 1))
    assert abs(r["edge_cents_per_contract"] - mean) < 1e-9
    assert abs(r["se_cents"] - sd / math.sqrt(len(xs))) < 1e-9
    assert abs(r["z"] - mean / (sd / math.sqrt(len(xs)))) < 1e-9


# ── counting ─────────────────────────────────────────────────────────

def test_progress_excludes_voids_and_reports_both(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl",
           [_row("w", "WIN"), _row("l", "LOSS"), _row("v", "VOID")])
    r = cp.evaluate(cp.load_terminal(_logs(tmp_path)))
    assert r["progress"] == 2
    assert r["n_voids"] == 1
    assert r["n_terminal_incl_voids"] == 3


def test_archives_and_gzip_are_read(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("live")])
    _write(tmp_path / "data/trade_logs/archive/trade_log.archive_old.jsonl",
           [_row("arch")])
    _write(tmp_path / "data/trade_logs/trade_log.1.jsonl.gz", [_row("gz")], gz=True)
    rows = cp.load_terminal(_logs(tmp_path))
    assert {r["fingerprint"] for r in rows} == {"live", "arch", "gz"}


def test_duplicates_do_not_inflate_progress(tmp_path):
    row = _row("dupe")
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [row, dict(row)])
    _write(tmp_path / "data/trade_logs/archive/trade_log.archive_old.jsonl",
           [dict(row)])
    assert cp.evaluate(cp.load_terminal(_logs(tmp_path)))["progress"] == 1


def test_other_pods_are_ignored(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl",
           [_row("a"), _row("b", pod="P-001"), _row("c", pod="P-015")])
    assert cp.evaluate(cp.load_terminal(_logs(tmp_path)))["progress"] == 1


def test_unsettled_rows_are_ignored(tmp_path):
    open_row = _row("open")
    open_row["outcome"] = "PLACED"
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), open_row])
    assert cp.evaluate(cp.load_terminal(_logs(tmp_path)))["progress"] == 1


# ── pricing primitives ───────────────────────────────────────────────

def test_entry_price_falls_back_to_venue_prob_because_fill_price_is_null():
    assert cp.entry_price(_row("a", venue_prob=0.275)) == 0.275


def test_entry_price_is_never_fabricated():
    bare = _row("a")
    for k in ("fill_price", "venue_prob", "kalshi_prob"):
        bare[k] = None
    assert cp.entry_price(bare) is None
    assert cp.entry_price({"fill_price": 0.0}) is None
    assert cp.entry_price({"fill_price": 1.0}) is None
    assert cp.entry_price({"venue_prob": "nonsense"}) is None


# ── shape the manager depends on ─────────────────────────────────────

def test_json_output_carries_progress_threshold_and_the_rule(tmp_path, capsys):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), _row("b")])
    cp.main(["--json"] + sum([["--log", p] for p in _logs(tmp_path)], []))
    out = json.loads(capsys.readouterr().out)
    assert out["progress"] == 2 and out["threshold"] == 500
    assert out["verdict"] == "NO DECISION"          # n < 500
    assert out["rule_document"] == "research/P014_DECISION_RULE.md"
    assert out["locked_date"] == "2026-07-31"
    assert "edge_cents_per_contract" in out and "z" in out


def test_human_output_names_the_locked_rule(tmp_path, capsys):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a")])
    cp.main(sum([["--log", p] for p in _logs(tmp_path)], []))
    text = capsys.readouterr().out
    assert "LOCKED 2026-07-31" in text
    assert "VERDICT: NO DECISION" in text
