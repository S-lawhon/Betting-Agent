#!/usr/bin/env python3
"""Sanctioned forward reader for P-029 Gate 0c.

The reader stays blind before the preregistered read date. After that date it
reprices the forward shadow tape from raw leg marks with the frozen model,
joins the immutable settlement archive, and evaluates both Gate 0c conditions.
It never mutates either input.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

POD_ID = "P-029"
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.combo_copula import ComboCopula, correlation_matrix  # noqa: E402


DEFAULT_DB = Path("/var/lib/p029/shadow.sqlite")
DEFAULT_ARCHIVE = Path("/var/lib/p029/archive")
DEFAULT_MODEL = ROOT / "combo_research" / "recalib" / "recalibrated_model.json"

MODEL_SHA256 = "01411d863de04075a38f02b40b7e0c4a7e21463a96b42d8194e8ccd8325956af"
WINDOW_START = "2026-08-06T00:00:00Z"
WINDOW_END = "2026-08-19T23:59:59Z"
EXTENSION_WINDOW_END = "2026-08-26T23:59:59Z"
READ_NOT_BEFORE = datetime(2026, 8, 23, tzinfo=timezone.utc)
EXTENSION_READ_NOT_BEFORE = datetime(2026, 8, 30, tzinfo=timezone.utc)

KNOWN_TAPE_GAPS = [{
    "start_utc": "2026-08-07T00:17:11Z",
    "end_utc": "2026-08-07T00:23:01Z",
    "duration_seconds": 350,
    "cause": "Gate 0c deployment temporarily removed p029 checkout traversal",
    "backfillable": False,
}]

MIN_LEGS, MAX_LEGS = 2, 4
MIN_PRICE, MAX_PRICE = 0.10, 0.35
MIN_N = 500
MARGIN_CONTINUE_C = 2.0
MARGIN_STOP_C = 1.0
REALIZED_CONTINUE_C = 1.0
BOOTSTRAP_SEED = 20260805


def _parse_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _model(path: Path) -> Tuple[Dict[str, Any], ComboCopula]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError(
            f"frozen model hash mismatch: expected {MODEL_SHA256}, got {digest}"
        )
    model = json.loads(raw)
    # These common random numbers match the recalibration report's mixed-row
    # pricer. Parameters still come exclusively from the frozen JSON artifact.
    return model, ComboCopula(draws=8_000, max_legs=4,
                              seed=20260729, rho=model["rho"])


def _calibrated_side_prob(mid: float, side: str, a: float, b: float) -> float:
    p = mid if (side or "yes").lower() == "yes" else 1.0 - mid
    p = min(1.0 - 1e-4, max(1e-4, p))
    z = a + b * math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-z))


def price_from_marks(legs_json: str, marks_json: str,
                     model: Dict[str, Any], copula: ComboCopula) -> Optional[float]:
    try:
        legs = [(str(t), str(s).lower()) for t, s in json.loads(legs_json)]
        marks = json.loads(marks_json)
    except (TypeError, ValueError):
        return None
    if not (MIN_LEGS <= len(legs) <= MAX_LEGS):
        return None
    by_ticker = {m.get("ticker"): m for m in marks if isinstance(m, dict)}
    a = float(model["leg_map"]["a"])
    b = float(model["leg_map"]["b"])
    eff: List[float] = []
    tickers: List[str] = []
    sides: List[str] = []
    for ticker, side in legs:
        mid = (by_ticker.get(ticker) or {}).get("yes_mid")
        if mid is None:
            return None
        eff.append(_calibrated_side_prob(float(mid), side, a, b))
        tickers.append(ticker)
        sides.append(side)
    corr = correlation_matrix(tickers, model["rho"], sides=sides)
    return copula.joint_yes(eff, corr)


def load_forward_rows(db_path: Path, model: Dict[str, Any], copula: ComboCopula,
                      window_end: str = WINDOW_END,
                      ) -> Tuple[List[Dict[str, Any]], int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT market_ticker, event_ticker, seen_ts, legs_json,"
            " leg_marks_json, first_trade_px, traded FROM combo"
            " WHERE datetime(seen_ts) >= datetime(?)"
            " AND datetime(seen_ts) <= datetime(?)",
            (WINDOW_START, window_end),
        ).fetchall()
    finally:
        con.close()

    out: List[Dict[str, Any]] = []
    unpriceable = 0
    for ticker, event, seen_ts, legs_json, marks_json, first_px, traded in rows:
        fair = price_from_marks(legs_json, marks_json, model, copula)
        if fair is None:
            unpriceable += 1
            continue
        out.append({
            "ticker": ticker,
            "event": event or "_none",
            "day": str(seen_ts)[:10],
            "first_px": float(first_px) if first_px is not None else None,
            "traded": bool(traded and first_px is not None),
            "in_zone": MIN_PRICE <= fair <= MAX_PRICE,
            "fair": fair,
        })
    return out, unpriceable


def _settlement_value(row: Dict[str, Any]) -> Optional[float]:
    value = row.get("settlement_value_dollars")
    if value is not None:
        return float(value)
    result = str(row.get("result") or "").lower()
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    return None


def load_settlements(archive_dir: Path, wanted: set[str]) -> Dict[str, float]:
    settled: Dict[str, float] = {}
    for path in sorted(archive_dir.rglob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    ticker = row.get("ticker")
                    if ticker not in wanted or ticker in settled:
                        continue
                    value = _settlement_value(row)
                    if value is not None:
                        settled[ticker] = value
        except OSError:
            continue
        if len(settled) == len(wanted):
            break
    return settled


def cluster_bootstrap(values: Sequence[Tuple[str, float]], statistic: Callable,
                      iterations: int, seed: int = BOOTSTRAP_SEED
                      ) -> Optional[List[float]]:
    by_cluster: Dict[str, List[float]] = {}
    for cluster, value in values:
        by_cluster.setdefault(cluster or "_none", []).append(value)
    clusters = sorted(by_cluster)
    if len(clusters) < 2 or iterations <= 0:
        return None
    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(iterations):
        sample: List[float] = []
        for _ in clusters:
            sample.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        draws.append(float(statistic(sample)))
    draws.sort()
    return [draws[int(0.025 * len(draws))],
            draws[min(len(draws) - 1, int(0.975 * len(draws)))]]


def decide(n: int, margin_c: float, realized_c: float,
           realized_ci: Optional[Sequence[float]], *, extension_final: bool = False
           ) -> Tuple[str, str]:
    if margin_c < MARGIN_STOP_C:
        return "STOP", f"margin {margin_c:+.2f}c is below +{MARGIN_STOP_C:.1f}c"
    if realized_c <= 0.0:
        return "STOP", f"realized seller edge {realized_c:+.2f}c is <= 0"
    if n < MIN_N:
        return "EXTEND", f"only {n} settled in-zone rows; need at least {MIN_N}"
    realized_pass = (
        realized_c >= REALIZED_CONTINUE_C
        and realized_ci is not None
        and realized_ci[0] > 0.0
    )
    if margin_c >= MARGIN_CONTINUE_C and realized_pass:
        return "CONTINUE", "both preregistered conditions pass"
    if extension_final:
        return ("INCONCLUSIVE",
                "mixed result after the single preregistered extension; "
                "no further extension is authorized")
    return "EXTEND", "mixed result; the single preregistered extension applies"


def evaluate(db_path: Path, archive_dir: Path, model_path: Path,
             now: datetime, boot: int) -> Dict[str, Any]:
    extension_final = now >= EXTENSION_READ_NOT_BEFORE
    window_end = EXTENSION_WINDOW_END if extension_final else WINDOW_END
    base = {
        "pod": POD_ID,
        "metric": "gate0c_forward_dual_condition",
        "progress": None,
        "threshold": MIN_N,
        "model_sha256": MODEL_SHA256,
        "window_start_utc": WINDOW_START,
        "window_end_utc": window_end,
        "read_not_before": READ_NOT_BEFORE.date().isoformat(),
        "extension_read_not_before": EXTENSION_READ_NOT_BEFORE.date().isoformat(),
        "extension_final": extension_final,
        "data_qualification": {
            "known_tape_gaps": KNOWN_TAPE_GAPS,
            "collection_complete": False,
        },
    }
    if now < READ_NOT_BEFORE:
        return base | {
            "verdict": "NO DECISION",
            "reason": "reader remains blind until 2026-08-23",
        }
    if not db_path.exists():
        return base | {
            "verdict": "UNAVAILABLE",
            "reason": f"shadow database not found: {db_path}",
        }
    if not archive_dir.exists():
        return base | {
            "verdict": "UNAVAILABLE",
            "reason": f"settlement archive not found: {archive_dir}",
        }

    model, copula = _model(model_path)
    all_rows, unpriceable = load_forward_rows(
        db_path, model, copula, window_end=window_end)
    settlements = load_settlements(archive_dir, {r["ticker"] for r in all_rows})
    model_health_rows = [r | {"settlement": settlements[r["ticker"]]}
                         for r in all_rows if r["ticker"] in settlements]
    decision_rows = [r for r in all_rows if r["traded"] and r["in_zone"]]
    matched = [r | {"settlement": settlements[r["ticker"]]}
               for r in decision_rows if r["ticker"] in settlements]
    n = len(matched)
    base["progress"] = n
    base.update({
        "traded_in_zone": len(decision_rows),
        "unsettled_in_zone": len(decision_rows) - n,
        "unpriceable": unpriceable,
        "model_health_n": len(model_health_rows),
    })
    if not matched:
        return base | {
            "verdict": "NO DECISION",
            "reason": "no settled, traded in-zone rows in the forward window",
        }

    margins = [(r["first_px"] - r["fair"]) * 100.0 for r in matched]
    realized = [(r["first_px"] - r["settlement"]) * 100.0 for r in matched]
    bias = [(r["settlement"] - r["fair"]) * 100.0
            for r in model_health_rows]
    margin_c = float(median(margins))
    realized_c = float(mean(realized))
    margin_event_ci = cluster_bootstrap(
        [(r["event"], x) for r, x in zip(matched, margins)], median, boot)
    margin_day_ci = cluster_bootstrap(
        [(r["day"], x) for r, x in zip(matched, margins)], median, boot,
        seed=BOOTSTRAP_SEED + 1)
    realized_day_ci = cluster_bootstrap(
        [(r["day"], x) for r, x in zip(matched, realized)], mean, boot,
        seed=BOOTSTRAP_SEED + 2)
    verdict, reason = decide(
        n, margin_c, realized_c, realized_day_ci,
        extension_final=extension_final)
    model_bias = float(mean(bias))
    return base | {
        "margin_median_c": margin_c,
        "margin_event_ci95_c": margin_event_ci,
        "margin_day_ci95_c": margin_day_ci,
        "realized_seller_mean_c": realized_c,
        "realized_day_ci95_c": realized_day_ci,
        "model_bias_mean_c": model_bias,
        "model_health_refit_required": abs(model_bias) > 1.0,
        "verdict": verdict,
        "reason": reason,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--boot", type=int, default=2_000)
    parser.add_argument("--now", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = evaluate(args.db, args.archive, args.model,
                          _parse_time(args.now), args.boot)
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        result = {
            "pod": POD_ID,
            "metric": "gate0c_forward_dual_condition",
            "progress": None,
            "threshold": MIN_N,
            "verdict": "UNAVAILABLE",
            "reason": str(exc),
        }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"P-029 Gate 0c: {result['verdict']}")
        print(result.get("reason", ""))
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
