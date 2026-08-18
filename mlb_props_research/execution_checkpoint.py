#!/usr/bin/env python3
"""Sanctioned, outcome-blind reader for the MLB props execution gate."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SERIES = "KXMLBHIT"
CLEAN_START = date(2026, 7, 22)
READ_NOT_BEFORE = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)
MIN_DAYS = 27
MIN_COVERAGE = 0.90
WINDOW_MINUTES = 30.0
MIN_ASK = 0.15
MAX_ASK = 0.45
MIN_VOLUME = 50.0
MAX_SPREAD = 0.06
MAX_SIZE = 100.0
MIN_DAILY_PNL = 10.0
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
RULE_DOCUMENT = "mlb_props_research/MLB_PROPS_EXECUTION_RULE.md"
RULE_SHA256 = "c76296909b878b70de7eab4a82fb24ee0c7c45158ae0e1d8bb6ed7cc589c8c60"

MONTHS = {name: number for number, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
TICKER_RE = re.compile(
    r"^(KXMLB[A-Z]+)-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")


def parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def start_of(ticker: str) -> Optional[datetime]:
    match = TICKER_RE.match(ticker or "")
    if not match:
        return None
    _, yy, month, day, hour, minute = match.groups()
    try:
        local = datetime(2000 + int(yy), MONTHS[month], int(day),
                         int(hour), int(minute), tzinfo=ET)
    except (KeyError, ValueError):
        return None
    return local.astimezone(UTC)


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def snapshot_files(live_dir: Path) -> list[Path]:
    return [path for path in sorted(live_dir.glob("snapshots_*.jsonl"))
            if not path.stem.endswith("_mac")]


def scan_snapshots(paths: Iterable[Path]) -> Dict[str, Any]:
    """Return candidate tickers and their final observation inside the window."""
    markets: Dict[str, Dict[str, Any]] = {}
    malformed = 0
    files = 0
    for path in paths:
        files += 1
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    malformed += 1
                    continue
                if not isinstance(row, dict) or row.get("t") != "mkt":
                    continue
                ticker = row.get("tk")
                if not isinstance(ticker, str) or not ticker.startswith(SERIES):
                    continue
                start = start_of(ticker)
                observed = parse_time(row.get("ts"))
                if start is None or observed is None:
                    malformed += 1
                    continue
                scheduled_day = start.astimezone(ET).date()
                if scheduled_day < CLEAN_START:
                    continue
                market = markets.setdefault(ticker, {
                    "ticker": ticker,
                    "start_utc": start,
                    "day": scheduled_day,
                    "window": None,
                })
                minutes = (start - observed).total_seconds() / 60.0
                if not 0.0 <= minutes <= WINDOW_MINUTES:
                    continue
                previous = market["window"]
                if previous is None or minutes < previous["minutes_before"]:
                    market["window"] = {
                        "ticker": ticker,
                        "day": scheduled_day.isoformat(),
                        "start_utc": start.isoformat().replace("+00:00", "Z"),
                        "observed_utc": observed.isoformat().replace("+00:00", "Z"),
                        "minutes_before": minutes,
                        "yes_bid": _number(row.get("yb")),
                        "yes_ask": _number(row.get("ya")),
                        "ask_size": _number(row.get("yas")),
                        "volume": _number(row.get("vol")),
                    }
    return {"markets": markets, "files_scanned": files,
            "malformed_rows": malformed}


def is_entry(row: Mapping[str, Any]) -> bool:
    bid = _number(row.get("yes_bid"))
    ask = _number(row.get("yes_ask"))
    size = _number(row.get("ask_size"))
    volume = _number(row.get("volume"))
    return bool(
        bid is not None and ask is not None and size is not None
        and volume is not None and 0.0 < bid < ask
        and MIN_ASK <= ask <= MAX_ASK and size > 0.0
        and volume >= MIN_VOLUME and ask - bid <= MAX_SPREAD + 1e-12
    )


def qualify_days(scan: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    grouped: Dict[date, list[Dict[str, Any]]] = defaultdict(list)
    for market in scan["markets"].values():
        grouped[market["day"]].append(market)

    today_et = now.astimezone(ET).date()
    rows = []
    entries_by_day: Dict[str, list[Dict[str, Any]]] = {}
    for day in sorted(grouped):
        markets = grouped[day]
        window_rows = [market["window"] for market in markets
                       if market.get("window") is not None]
        entries = [dict(row) for row in window_rows if is_entry(row)]
        coverage = len(window_rows) / len(markets) if markets else 0.0
        reasons = []
        if day >= today_et:
            reasons.append("day_incomplete")
        if coverage < MIN_COVERAGE:
            reasons.append("window_coverage_below_90pct")
        if not entries:
            reasons.append("no_qualifying_entries")
        admissible = not reasons
        day_key = day.isoformat()
        if admissible:
            entries_by_day[day_key] = entries
        rows.append({
            "day": day_key,
            "candidate_markets": len(markets),
            "window_markets": len(window_rows),
            "coverage": round(coverage, 6),
            "qualifying_entries": len(entries),
            "admissible": admissible,
            "exclusion_reasons": reasons,
        })
    return {"days": rows, "entries_by_day": entries_by_day}


def progress_payload(qualified: Mapping[str, Any], scan: Mapping[str, Any],
                     now: datetime) -> Dict[str, Any]:
    admissible = sorted(qualified["entries_by_day"])
    selected = admissible[:MIN_DAYS]
    payload = {
        "id": "R-MLB-PROPS",
        "metric": "clean_execution_game_days",
        "progress": len(admissible),
        "threshold": MIN_DAYS,
        "verdict": "NO DECISION",
        "rule_document": RULE_DOCUMENT,
        "rule_sha256": RULE_SHA256,
        "read_not_before_utc": READ_NOT_BEFORE.isoformat().replace("+00:00", "Z"),
        "clean_start_et": CLEAN_START.isoformat(),
        "selected_days": selected,
        "first_27_frozen": len(selected) == MIN_DAYS,
        "files_scanned": scan["files_scanned"],
        "malformed_rows": scan["malformed_rows"],
        "day_qualification": qualified["days"],
        "generated_at_utc": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "outcomes_read": False,
    }
    if now < READ_NOT_BEFORE:
        payload["reason"] = "reader remains outcome-blind before 2026-08-18 00:30 ET"
    elif len(selected) < MIN_DAYS:
        payload["reason"] = (
            f"{len(selected)} of {MIN_DAYS} admissible completed ET game-days; "
            "outcomes remain unread")
    else:
        payload["reason"] = "sample is frozen; settlements are required"
    return payload


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_settlements(path: Path) -> Dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(ticker): str(result).lower() for ticker, result in payload.items()
            if str(result).lower() in {"yes", "no", "scalar"}}


def fetch_result(ticker: str, retries: int = 3) -> Optional[str]:
    url = f"{BASE_URL}/markets/{quote(ticker, safe='')}"
    request = Request(url, headers={"User-Agent": "betting-pod-shop/mlb-props-gate"})
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed host
                payload = json.loads(response.read().decode("utf-8"))
            result = str((payload.get("market") or {}).get("result") or "").lower()
            return result if result in {"yes", "no", "scalar"} else None
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
    return None


def fill_settlements(entries_by_day: Mapping[str, list[Dict[str, Any]]],
                     selected_days: Sequence[str], cache_path: Path,
                     fetch: bool) -> tuple[Dict[str, str], list[str]]:
    results = load_settlements(cache_path)
    tickers = sorted({entry["ticker"] for day in selected_days
                      for entry in entries_by_day[day]})
    missing = [ticker for ticker in tickers if ticker not in results]
    if fetch:
        for ticker in missing:
            result = fetch_result(ticker)
            if result is not None:
                results[ticker] = result
            time.sleep(0.25)
        _write_atomic(cache_path, results)
        missing = [ticker for ticker in tickers if ticker not in results]
    return results, missing


def evaluate(entries_by_day: Mapping[str, list[Dict[str, Any]]],
             selected_days: Sequence[str], settlements: Mapping[str, str],
             base: Mapping[str, Any]) -> Dict[str, Any]:
    daily = []
    all_returns = []
    total_contracts = 0.0
    total_pnl = 0.0
    for day in selected_days:
        returns = []
        day_contracts = 0.0
        day_pnl = 0.0
        for entry in entries_by_day[day]:
            ask = float(entry["yes_ask"])
            result = 1.0 if settlements[entry["ticker"]] == "yes" else 0.0
            net = result - ask - 0.07 * ask * (1.0 - ask)
            size = min(float(entry["ask_size"]), MAX_SIZE)
            returns.append(net)
            all_returns.append(net)
            day_contracts += size
            day_pnl += net * size
        daily.append({
            "day": day,
            "entries": len(returns),
            "net_per_contract": sum(returns) / len(returns),
            "displayed_contracts_capped": day_contracts,
            "displayed_pnl_usd": day_pnl,
        })
        total_contracts += day_contracts
        total_pnl += day_pnl

    daily_means = [row["net_per_contract"] for row in daily]
    mean = sum(daily_means) / len(daily_means)
    variance = sum((value - mean) ** 2 for value in daily_means) / (
        len(daily_means) - 1)
    se = math.sqrt(variance / len(daily_means))
    lower, upper = mean - 1.96 * se, mean + 1.96 * se
    mean_daily_pnl = total_pnl / len(daily)
    verdict = ("BUILD_CANDIDATE"
               if lower > 0.0 and mean_daily_pnl >= MIN_DAILY_PNL else "STOP")
    reason = (
        f"daily-equal net {mean * 100:+.2f}c, 95% CI "
        f"[{lower * 100:+.2f}c, {upper * 100:+.2f}c], displayed-capacity "
        f"P&L {mean_daily_pnl:+.2f}/day")
    return dict(base) | {
        "verdict": verdict,
        "reason": reason,
        "outcomes_read": True,
        "days": len(daily),
        "entries": len(all_returns),
        "daily_equal_net_per_contract": mean,
        "daily_standard_error": se,
        "ci95_lower": lower,
        "ci95_upper": upper,
        "market_weighted_net_per_contract": sum(all_returns) / len(all_returns),
        "displayed_contracts_capped": total_contracts,
        "displayed_pnl_usd": total_pnl,
        "mean_daily_displayed_pnl_usd": mean_daily_pnl,
        "daily_results": daily,
        "authorization": "build proposal only; no credentials, orders, or capital",
    }


def run(live_dir: Path, now: datetime, settlements_path: Path,
        fetch: bool = False) -> Dict[str, Any]:
    scan = scan_snapshots(snapshot_files(live_dir))
    qualified = qualify_days(scan, now)
    base = progress_payload(qualified, scan, now)
    if now < READ_NOT_BEFORE or base["progress"] < MIN_DAYS:
        return base
    days = base["selected_days"]
    settlements, missing = fill_settlements(
        qualified["entries_by_day"], days, settlements_path, fetch)
    selected_tickers = sorted({
        entry["ticker"] for day in days
        for entry in qualified["entries_by_day"][day]
    })
    scalar = [ticker for ticker in selected_tickers
              if settlements.get(ticker) == "scalar"]
    if scalar:
        return base | {
            "verdict": "UNSCORABLE",
            "reason": (
                f"{len(scalar)} selected market(s) finalized scalar; the locked "
                "rule requires binary outcomes and authorizes no substitution "
                "or sample extension"
            ),
            "scalar_settlements": scalar,
            "outcomes_read": True,
            "authorization": "none; successor study requires a new locked rule",
        }
    if missing:
        return base | {
            "verdict": "RESULTS_PENDING",
            "reason": f"{len(missing)} selected market settlement(s) unavailable",
            "settlements_missing": len(missing),
            "outcomes_read": True,
        }
    return evaluate(qualified["entries_by_day"], days, settlements, base)


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError("--now must be an ISO-8601 timestamp with timezone")
    return parsed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    data = Path(__file__).resolve().parent / "data"
    parser.add_argument("--live-dir", type=Path, default=data / "live")
    parser.add_argument("--output-dir", type=Path, default=data / "execution_gate")
    parser.add_argument("--fetch-results", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        at = _parse_now(args.now)
        result = run(args.live_dir, at, args.output_dir / "settlements.json",
                     fetch=args.fetch_results)
        if args.persist:
            stamp = at.strftime("%Y-%m-%dT%H%M%SZ")
            _write_atomic(args.output_dir / f"checkpoint_{stamp}.json", result)
            _write_atomic(args.output_dir / "latest.json", result)
    except (OSError, ValueError) as exc:
        print(f"MLB props checkpoint failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"MLB props execution gate: {result['progress']}/{MIN_DAYS}")
        print(f"VERDICT: {result['verdict']} - {result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
