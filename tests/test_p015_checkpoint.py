"""
tests/test_p015_checkpoint.py
─────────────────────────────
Tests for the P-015 gate reader (`scripts/p015_checkpoint.py`).

The regression under test is not the statistic — §"Test statistic" of
`tennis_research/P015_DECISION_RULE.md` was always implemented correctly — but
**whether the reader can reach the pod's rows at all.**

Until 2026-07-28 it defaulted to `data/pods/P-015.jsonl`, a path that has never
existed on the droplet. `load_settled` returned `[]` for the missing file, so
the gate reported `n = 0, NO DECISION` while five genuinely settled trades sat
in `trade_log.jsonl` — and `manager/collect.py` relayed the zero faithfully,
because delegating to the sanctioned reader is the correct house pattern.

The locked rule names **this script** as the only sanctioned reader. It does not
name a log path and does not define an observation by which file a row is in, so
repointing it is a defect fix; the thresholds, the statistic and the VOID
exclusion are untouched and are asserted below to prove it.
"""
import gzip
import importlib.util
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SPEC = importlib.util.spec_from_file_location(
    "p015_checkpoint",
    Path(__file__).resolve().parent.parent / "scripts" / "p015_checkpoint.py")
cp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cp)


def _trade(fp, outcome="WIN", price=0.92, pod="P-015", market="KXATPMATCH-A"):
    """A row in the exact shape the engine writes to trade_log.jsonl."""
    return {
        "fingerprint": fp, "pod_id": pod, "market_id": market,
        "outcome": outcome, "fill_price": price, "pnl_usd": 1.3,
        "settled_at_utc": "2026-07-25T15:35:20.316190+00:00",
    }


def _write(path: Path, rows, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = (lambda: gzip.open(path, "wt")) if gz else (lambda: open(path, "w"))
    with opener() as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return str(path)


# ── the regression ───────────────────────────────────────────────────

def test_reader_finds_rows_in_the_trade_log(tmp_path):
    """The defect: five real settled trades, gate reported zero."""
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [
        _trade("f1", "WIN", 0.92), _trade("f2", "WIN", 0.93),
        _trade("f3", "WIN", 0.85), _trade("f4", "WIN", 0.94),
        _trade("f5", "LOSS", 0.87),
    ])
    trades = cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])
    assert len(trades) == 5
    assert cp.evaluate(trades)["n"] == 5


def test_a_missing_default_path_is_not_an_error(tmp_path):
    """`data/pods/P-015.jsonl` is still in the defaults and still absent. It
    must contribute nothing rather than short-circuiting the whole read."""
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_trade("f1")])
    assert not (tmp_path / "data/pods/P-015.jsonl").exists()
    assert len(cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])) == 1


def test_archived_and_gzipped_logs_are_read(tmp_path):
    """Rotation truncates the live file; reading only it under-reports."""
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_trade("live")])
    _write(tmp_path / "data/trade_logs/archive/trade_log.archive_old.jsonl",
           [_trade("arch")])
    _write(tmp_path / "data/trade_logs/trade_log.1.jsonl.gz", [_trade("gz")], gz=True)
    trades = cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])
    assert {t["fingerprint"] for t in trades} == {"live", "arch", "gz"}


def test_duplicates_across_logs_do_not_inflate_n(tmp_path):
    """n is measured against a PRE-REGISTERED threshold, so a rotated copy
    double-counting is not cosmetic — it moves the gate."""
    row = _trade("dupe")
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [row, dict(row)])
    _write(tmp_path / "data/trade_logs/archive/trade_log.archive_old.jsonl",
           [dict(row)])
    assert len(cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])) == 1


def test_default_sources_exclude_backup_and_skip_only_files(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [_trade("live")])
    _write(tmp_path / "data/trade_logs/trade_log_backup.jsonl", [_trade("backup")])
    _write(tmp_path / "data/trade_logs/archive/skipped_archive_20260420.jsonl",
           [_trade("skipped")])
    trades = cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])
    assert [row["fingerprint"] for row in trades] == ["live"]


def test_other_pods_are_ignored(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [
        _trade("a", pod="P-015"), _trade("b", pod="P-001"),
        _trade("c", pod="P-017")])
    assert len(cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])) == 1


def test_explicit_log_override_still_works(tmp_path):
    p = _write(tmp_path / "custom.jsonl", [_trade("x")])
    assert len(cp.load_settled([p])) == 1
    assert len(cp.load_settled(p)) == 1          # bare string, not a list


# ── the locked rule must be untouched ────────────────────────────────

def test_voids_are_excluded_no_risk_was_taken(tmp_path):
    _write(tmp_path / "data/trade_logs/trade_log.jsonl", [
        _trade("w", "WIN"), _trade("l", "LOSS"), _trade("v", "VOID")])
    trades = cp.load_settled([str(tmp_path / p) for p in cp.DEFAULT_LOGS])
    assert len(trades) == 2


def test_statistic_matches_the_locked_rule_by_hand():
    """§Test statistic: edge = mean(won) − mean(fill_price + taker_fee),
    se = sqrt(hit(1−hit)/n), z = edge/se. Computed independently here."""
    trades = [_trade("a", "WIN", 0.90), _trade("b", "WIN", 0.90),
              _trade("c", "LOSS", 0.90), _trade("d", "WIN", 0.90)]
    r = cp.evaluate(trades)
    fee = math.ceil(0.07 * 0.90 * 0.10 * 100.0) / 100.0     # 0.01
    hit, be = 0.75, 0.90 + fee
    assert r["hit"] == hit
    assert abs(r["breakeven"] - be) < 1e-12
    assert abs(r["edge"] - (hit - be)) < 1e-12
    assert abs(r["z"] - (hit - be) / math.sqrt(hit * (1 - hit) / 4)) < 1e-9


def test_thresholds_are_unchanged():
    assert (cp.MIN_N_DECISION, cp.MIN_N_PROMOTE) == (120, 240)
    assert (cp.PROMOTE_Z, cp.HARD_KILL_Z) == (2.0, -2.0)


def test_five_trades_is_still_no_decision():
    """Finding the rows must not move the verdict. n=5 << 120: the rule says
    underpowered, and 'silence is not confirmation' cuts both ways."""
    r = cp.evaluate([_trade(str(i), "WIN", 0.92) for i in range(5)])
    assert r["n"] == 5 and r["verdict"] == "NO DECISION"


def test_hard_kill_still_fires_at_any_n():
    r = cp.evaluate([_trade(str(i), "LOSS", 0.92) for i in range(30)])
    assert r["verdict"] == "HARD KILL"


# ── never invent a price ─────────────────────────────────────────────

def test_a_row_with_no_price_is_excluded_not_defaulted_to_0_9():
    """The second bug in this reader: `fill_price or venue_prob or 0.9` fed a
    FABRICATED 0.9 breakeven into a pre-registered statistic whenever a row
    carried neither price."""
    good = _trade("g", "WIN", 0.92)
    bad = _trade("b", "WIN", 0.92)
    bad.pop("fill_price")
    r = cp.evaluate([good, bad])
    assert r["n"] == 1
    assert r["unpriced"] == 1
    assert abs(r["breakeven"] - (0.92 + cp.taker_fee(0.92))) < 1e-12


def test_all_rows_unpriced_yields_no_decision_not_a_fabricated_edge():
    bad = _trade("b", "WIN", 0.92)
    bad.pop("fill_price")
    r = cp.evaluate([bad])
    assert r["verdict"] == "NO DECISION"
    assert r["unpriced"] == 1
    assert "invent" in r["reason"]


def test_out_of_range_prices_are_not_used():
    """0.0 and 1.0 are not tradeable fills; treat them as unpriced rather than
    letting a sentinel through into the breakeven."""
    assert cp.entry_price({"fill_price": 0.0}) is None
    assert cp.entry_price({"fill_price": 1.0}) is None
    assert cp.entry_price({"fill_price": None, "venue_prob": 0.9}) == 0.9
    assert cp.entry_price({"fill_price": "nonsense"}) is None


def test_output_carries_the_keys_the_manager_reads():
    """`manager.checks._gate_progress` reads `progress`, not `n`.

    Emitting only `n` left the gate unreadable by the manager even after the
    reader itself was fixed: the reader found the trades and the gate machinery
    still saw None. Fixing the reader is not the same as fixing the gate.
    """
    for trades in ([], [_trade("a", "WIN", 0.92)]):
        r = cp.evaluate(trades)
        assert r["progress"] == r["n"]
        assert r["threshold"] == cp.MIN_N_DECISION == 120
