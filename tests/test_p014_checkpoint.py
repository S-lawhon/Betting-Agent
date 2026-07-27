"""
tests/test_p014_checkpoint.py
─────────────────────────────
Tests for the P-014 gate reader (`scripts/p014_checkpoint.py`).

P-014's gate declared `metric: settled_trades`, `source: trade_log`,
`threshold: 500` and **no reader**, so `_gate_progress` returned None by
construction and 345 settled rows were unstatable from a sanctioned number.

The property that matters most here is unusual, so it is asserted hardest:
**this reader must never emit a verdict.** P-014 has no pre-registered decision
rule — no kill line, no promotion condition, no hard-kill z — and a reader that
prints an edge next to a threshold is how criteria get decided after the fact.
P-013 cost $2,094 exactly that way.
"""
import gzip
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SPEC = importlib.util.spec_from_file_location(
    "p014_checkpoint",
    Path(__file__).resolve().parent.parent / "scripts" / "p014_checkpoint.py")
cp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cp)


def _row(fp, outcome="WIN", venue_prob=0.275, pod="P-014", pnl=5.69):
    """The exact shape P-014 writes: fill_price is NULL, entry is venue_prob."""
    return {
        "fingerprint": fp, "pod_id": pod, "market_id": "KXMLBGAME-A",
        "outcome": outcome, "fill_price": None, "venue_prob": venue_prob,
        "kalshi_prob": venue_prob, "fair_prob": 0.343, "pnl_usd": pnl,
        "settled_at_utc": "2026-07-25T19:35:22.063209+00:00",
    }


def _write(path: Path, rows, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda: gzip.open(path, "wt")) if gz else (lambda: open(path, "w"))
    with opener() as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return str(path)


def _logs(tmp_path):
    return [str(tmp_path / p) for p in cp.DEFAULT_LOGS]


# ── the reader must never judge ──────────────────────────────────────

def test_verdict_is_no_decision_at_every_n():
    """No pre-registered rule exists, so no n can produce a verdict — not even
    an n past the 500 threshold."""
    for n in (0, 1, 345, 499, 500, 5000):
        r = cp.evaluate([_row(str(i)) for i in range(min(n, 600))])
        assert r["verdict"] == "NO DECISION", n
        assert "no pre-registered decision rule" in r["reason"]


def test_evaluate_never_returns_an_edge_or_a_z():
    """The gate statistic must not leak into the default output, because that
    output is what the manager quotes in the daily brief."""
    r = cp.evaluate([_row(str(i), "WIN" if i % 2 else "LOSS") for i in range(50)])
    for banned in ("edge", "z", "hit", "breakeven", "pnl_usd"):
        assert banned not in r, banned


def test_action_required_names_the_missing_rule():
    r = cp.evaluate([_row("a")])
    assert r["rule_document"] is None
    assert "before n reaches 500" in r["action_required"].lower()
    assert "P-013" in r["action_required"]


def test_economics_are_available_but_only_on_request():
    rows = [_row(str(i), "WIN" if i < 30 else "LOSS", venue_prob=0.275)
            for i in range(50)]
    e = cp.economics(rows)
    assert e["n"] == 50 and e["wins"] == 30
    assert abs(e["hit"] - 0.60) < 1e-12
    assert "edge" in e and "z" in e


# ── counting ─────────────────────────────────────────────────────────

def test_progress_excludes_voids_and_reports_both(tmp_path):
    """The rule does not say whether VOIDs count. House convention excludes
    them; the with-VOID figure is surfaced so the ambiguity stays visible."""
    _write(tmp_path / "data/trade_logs/trade_log.jsonl",
           [_row("w", "WIN"), _row("l", "LOSS"), _row("v", "VOID")])
    r = cp.evaluate(cp.load_terminal(_logs(tmp_path)))
    assert r["progress"] == 2
    assert r["n_voids"] == 1
    assert r["n_terminal_incl_voids"] == 3


def test_archives_and_gzip_are_read(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("live")])
    _write(tmp_path / "data/trade_logs/archive/old.jsonl", [_row("arch")])
    _write(tmp_path / "data/trade_logs/trade_log.1.jsonl.gz", [_row("gz")], gz=True)
    rows = cp.load_terminal(_logs(tmp_path))
    assert {r["fingerprint"] for r in rows} == {"live", "arch", "gz"}


def test_duplicates_do_not_inflate_progress(tmp_path):
    row = _row("dupe")
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [row, dict(row)])
    _write(tmp_path / "data/trade_logs/archive/old.jsonl", [dict(row)])
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


# ── pricing ──────────────────────────────────────────────────────────

def test_entry_price_falls_back_to_venue_prob_because_fill_price_is_null():
    """345 of 345 live rows carry `fill_price: null`. A reader that assumed
    fill_price would price nothing at all."""
    assert cp.entry_price(_row("a", venue_prob=0.275)) == 0.275


def test_entry_price_is_never_fabricated():
    bare = _row("a")
    for k in ("fill_price", "venue_prob", "kalshi_prob"):
        bare[k] = None
    assert cp.entry_price(bare) is None
    assert cp.entry_price({"fill_price": 0.0}) is None
    assert cp.entry_price({"fill_price": 1.0}) is None
    assert cp.entry_price({"venue_prob": "nonsense"}) is None


def test_unpriced_settled_rows_are_counted(tmp_path):
    bad = _row("b")
    for k in ("fill_price", "venue_prob", "kalshi_prob"):
        bad[k] = None
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), bad])
    r = cp.evaluate(cp.load_terminal(_logs(tmp_path)))
    assert r["progress"] == 2 and r["n_unpriced"] == 1


# ── shape the manager depends on ─────────────────────────────────────

def test_json_output_carries_progress_and_threshold(tmp_path, capsys):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), _row("b")])
    cp.main(["--json"] + sum([["--log", p] for p in _logs(tmp_path)], []))
    out = json.loads(capsys.readouterr().out)
    assert out["progress"] == 2 and out["threshold"] == 500
    assert out["verdict"] == "NO DECISION"
    assert "economics" not in out


def test_unblind_adds_economics_and_only_then(tmp_path, capsys):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a"), _row("b")])
    logs = sum([["--log", p] for p in _logs(tmp_path)], [])
    cp.main(["--json", "--unblind"] + logs)
    out = json.loads(capsys.readouterr().out)
    assert "economics" in out and out["economics"]["n"] == 2
    assert out["verdict"] == "NO DECISION", "unblinding must not create a verdict"


def test_human_output_warns_when_unblinded(tmp_path, capsys):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_row("a")])
    logs = sum([["--log", p] for p in _logs(tmp_path)], [])
    cp.main(["--unblind"] + logs)
    text = capsys.readouterr().out
    assert "UNBLINDED" in text and "P-013" in text
    assert "ACTION REQUIRED" in text
