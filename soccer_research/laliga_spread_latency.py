#!/usr/bin/env python3
"""Forward-only La Liga spread latency collector and preregistered reader.

Governing rule: ``soccer_research/LALIGA_SPREAD_LATENCY_RULE.md`` (commit
20c418a). Public data only; this module has no authenticated or order path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import signal
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from src.kalshi_fees import fee_per_contract  # noqa: E402
from src.kalshi_public import KalshiPublic, fnum  # noqa: E402

UTC = timezone.utc
SERIES = "KXLALIGASPREAD"
ESPN_LEAGUE = "esp.1"
ESPN_BASE = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{ESPN_LEAGUE}"
RULE = "soccer_research/LALIGA_SPREAD_LATENCY_RULE.md"
SCHEMA = 1
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 20260818


def iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def timed_get(session: requests.Session, url: str, params: Mapping[str, Any]) -> tuple[dict | None, dict]:
    started = iso_now()
    status = None
    error = None
    payload = None
    try:
        response = session.get(url, params=dict(params), timeout=15)
        status = response.status_code
        if status == 200:
            payload = response.json()
        else:
            error = f"HTTP {status}"
    except (requests.RequestException, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    return payload, {
        "request_started_at": started, "received_at": iso_now(),
        "http_status": status, "error": error,
    }


def team_rows(summary: Mapping[str, Any]) -> list[dict]:
    competitions = ((summary.get("header") or {}).get("competitions") or [])
    competitors = (competitions[0].get("competitors") or []) if competitions else []
    rows = []
    for item in competitors:
        team = item.get("team") or {}
        rows.append({
            "id": str(team.get("id") or item.get("id") or ""),
            "abbreviation": str(team.get("abbreviation") or "").upper(),
            "name": str(team.get("displayName") or team.get("name") or ""),
            "home_away": str(item.get("homeAway") or ""),
            "score": item.get("score"),
        })
    return rows


def extract_key_events(summary: Mapping[str, Any]) -> list[dict]:
    """Return only preregistered events with source UTC wallclocks."""
    teams = {row["id"]: row for row in team_rows(summary)}
    out = []
    for item in summary.get("keyEvents") or []:
        kind = str((item.get("type") or {}).get("type") or "").lower()
        if item.get("scoringPlay"):
            event_kind = "goal"
        elif kind == "red-card" or bool(item.get("redCard")):
            event_kind = "red_card"
        elif kind == "kickoff":
            event_kind = "kickoff"
        else:
            continue
        wallclock = parse_time(item.get("wallclock"))
        if wallclock is None:
            continue
        team_id = str((item.get("team") or {}).get("id") or "")
        team = teams.get(team_id) or {}
        out.append({
            "id": str(item.get("id") or hashlib.sha256(json.dumps(
                item, sort_keys=True).encode()).hexdigest()[:20]),
            "kind": event_kind,
            "wallclock": wallclock.isoformat().replace("+00:00", "Z"),
            "team_id": team_id or None,
            "team_abbreviation": team.get("abbreviation") or None,
            "period": (item.get("period") or {}).get("number"),
            "clock_seconds": (item.get("clock") or {}).get("value"),
            "text": str(item.get("text") or "")[:500],
        })
    return out


def lineup_rows(summary: Mapping[str, Any]) -> list[dict]:
    rows = []
    for side in summary.get("rosters") or []:
        team = side.get("team") or {}
        starters = []
        for item in side.get("roster") or []:
            if not item.get("starter"):
                continue
            athlete = item.get("athlete") or {}
            starters.append({
                "id": str(athlete.get("id") or ""),
                "name": str(athlete.get("displayName") or ""),
            })
        rows.append({
            "team_id": str(team.get("id") or ""),
            "team_abbreviation": str(team.get("abbreviation") or "").upper(),
            "starters": sorted(starters, key=lambda row: row["id"]),
        })
    return rows


_SUBTITLE = re.compile(r"\b([A-Z0-9]{2,5})\s+VS\s+([A-Z0-9]{2,5})\b")


def match_kalshi_event(teams: Iterable[Mapping[str, Any]], kalshi_events: Iterable[Mapping[str, Any]]) -> dict | None:
    abbreviations = {str(row.get("abbreviation") or "").upper() for row in teams}
    abbreviations.discard("")
    matches = []
    for event in kalshi_events:
        found = _SUBTITLE.search(str(event.get("sub_title") or "").upper())
        if found and {found.group(1), found.group(2)} == abbreviations:
            matches.append(dict(event))
    return matches[0] if len(matches) == 1 else None


def normalize_levels(book: Mapping[str, Any]) -> dict:
    raw = book.get("orderbook_fp") or book.get("orderbook") or {}
    yes = raw.get("yes_dollars") or raw.get("yes") or []
    no = raw.get("no_dollars") or raw.get("no") or []

    def levels(values: Iterable[Any]) -> list[list[float]]:
        out = []
        for value in values:
            try:
                price, quantity = fnum(value[0]), fnum(value[1])
            except (IndexError, TypeError):
                continue
            if price is None or quantity is None:
                continue
            if price > 1.5:
                price /= 100.0
            out.append([round(price, 4), quantity])
        return sorted(out, reverse=True)

    return {"yes": levels(yes), "no": levels(no)}


def best_book(market: Mapping[str, Any]) -> dict | None:
    levels = market.get("book") or {}
    yes, no = levels.get("yes") or [], levels.get("no") or []
    if not yes or not no:
        return None
    bid, bid_qty = float(yes[0][0]), float(yes[0][1])
    ask, ask_qty = 1.0 - float(no[0][0]), float(no[0][1])
    if not 0 <= bid < ask <= 1:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "bid_qty": bid_qty, "ask_qty": ask_qty}


class Collector:
    def __init__(self, data_dir: Path, interval: float = 10.0):
        self.data_dir = data_dir
        self.interval = interval
        self.espn = requests.Session()
        self.espn.headers.update({"User-Agent": "betting-pod-shop/laliga-latency"})
        self.kalshi = KalshiPublic(min_interval=0.20)
        self.stop = False
        self._known_kalshi_events: dict[str, dict] = {}
        self._seen_trade_ids: set[str] = set()

    def _scoreboard(self, day: datetime) -> tuple[list[dict], list[dict]]:
        events, telemetry = [], []
        for delta in (-1, 0, 1):
            date = (day + timedelta(days=delta)).strftime("%Y%m%d")
            payload, timing = timed_get(
                self.espn, f"{ESPN_BASE}/scoreboard", {"dates": date})
            telemetry.append(dict(timing, date=date))
            events.extend((payload or {}).get("events") or [])
        by_id = {str(event.get("id")): event for event in events if event.get("id")}
        return list(by_id.values()), telemetry

    def _kalshi_events(self) -> tuple[list[dict], dict]:
        started = iso_now()
        error = None
        try:
            events = self.kalshi.open_events(SERIES)
            for event in events:
                ticker = str(event.get("event_ticker") or "")
                if ticker:
                    self._known_kalshi_events[ticker] = dict(event)
        except RuntimeError as exc:
            events, error = [], str(exc)
        # Kalshi removes an event from the open endpoint around the final
        # whistle. Retain its public metadata so the post-match status snapshot
        # can still complete the forward sample.
        merged = dict(self._known_kalshi_events)
        for event in events:
            ticker = str(event.get("event_ticker") or "")
            if ticker:
                merged[ticker] = dict(event)
        return list(merged.values()), {
            "request_started_at": started, "received_at": iso_now(),
            "error": error, "open_event_count": len(events),
            "cached_event_count": len(merged),
        }

    @staticmethod
    def _active(event: Mapping[str, Any], now: datetime) -> bool:
        scheduled = parse_time(event.get("date"))
        return bool(scheduled and scheduled - timedelta(minutes=90) <= now <=
                    scheduled + timedelta(minutes=210))

    def _new_recent_trades(self, trades: Iterable[Mapping[str, Any]],
                           now: datetime) -> list[dict]:
        """Keep the raw tape bounded while retaining the entire event window."""
        rows = []
        cutoff = now - timedelta(minutes=5)
        for trade in trades:
            trade_id = str(trade.get("trade_id") or hashlib.sha256(
                json.dumps(dict(trade), sort_keys=True).encode()).hexdigest())
            if trade_id in self._seen_trade_ids:
                continue
            self._seen_trade_ids.add(trade_id)
            created = parse_time(trade.get("created_time"))
            if created is not None and created >= cutoff:
                rows.append(dict(trade))
        return rows

    def collect_once(self) -> dict:
        cycle_started = iso_now()
        now = datetime.now(UTC)
        espn_events, scoreboard_timing = self._scoreboard(now)
        kalshi_events, kalshi_timing = self._kalshi_events()
        rows, errors = [], []
        active_events = [event for event in espn_events if self._active(event, now)]
        next_interval = self.interval if active_events else max(300.0, self.interval)
        for event in active_events:
            event_id = str(event.get("id") or "")
            summary, summary_timing = timed_get(
                self.espn, f"{ESPN_BASE}/summary", {"event": event_id})
            if summary is None:
                errors.append(f"summary unavailable: {event_id}")
                continue
            teams = team_rows(summary)
            kalshi_event = match_kalshi_event(teams, kalshi_events)
            if kalshi_event is None:
                errors.append(f"unmatched or ambiguous fixture: {event_id}")
                continue
            markets = []
            for market in kalshi_event.get("markets") or []:
                ticker = str(market.get("ticker") or "")
                if not ticker:
                    continue
                started = iso_now()
                raw_book = self.kalshi.get(
                    f"/markets/{ticker}/orderbook", {"depth": 20})
                book_received = iso_now()
                trades_raw = self.kalshi.get(
                    "/markets/trades", {"ticker": ticker, "limit": 100}) or {}
                trades_received = iso_now()
                suffix = ticker.rsplit("-", 1)[-1]
                markets.append({
                    "ticker": ticker,
                    "side_code": re.sub(r"[0-9.]+$", "", suffix),
                    "floor_strike": market.get("floor_strike"),
                    "price_ranges": market.get("price_ranges"),
                    "book_request_started_at": started,
                    "book_received_at": book_received,
                    "book": normalize_levels(raw_book or {}),
                    "trades_received_at": trades_received,
                    "trades": self._new_recent_trades(
                        trades_raw.get("trades") or [], now),
                })
            rows.append({
                "espn_event_id": event_id, "scheduled_at": event.get("date"),
                "status": ((summary.get("header") or {}).get("competitions") or
                           [{}])[0].get("status"),
                "teams": teams, "lineups": lineup_rows(summary),
                "key_events": extract_key_events(summary),
                "summary_timing": summary_timing,
                "kalshi_event_ticker": kalshi_event.get("event_ticker"),
                "kalshi_sub_title": kalshi_event.get("sub_title"),
                "markets": markets,
            })
        captured_at = iso_now()
        record = {
            "schema_version": SCHEMA, "rule_document": RULE,
            "cycle_started_at": cycle_started, "captured_at": captured_at,
            "scoreboard_timing": scoreboard_timing,
            "kalshi_events_timing": kalshi_timing,
            "active_fixture_count": len(active_events),
            "next_interval_seconds": next_interval,
            "matches": rows, "errors": errors,
        }
        day_path = self.data_dir / "snapshots" / f"{now.date().isoformat()}.jsonl"
        append_jsonl(day_path, record)
        atomic_json(self.data_dir / "status.json", {
            "schema_version": SCHEMA, "generated_at": captured_at,
            "status": "degraded" if errors else "healthy",
            "active_matches": len(rows), "market_books": sum(
                len(row["markets"]) for row in rows),
            "errors": errors, "snapshot_file": str(day_path),
            "active_interval_seconds": self.interval,
            "next_interval_seconds": next_interval,
            "safety": {"public_data_only": True, "orders_allowed": False},
        })
        return record

    def run(self) -> None:
        def stop(*_args):
            self.stop = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not self.stop:
            started = time.monotonic()
            try:
                record = self.collect_once()
            except Exception as exc:  # keep status observable; systemd restarts hard failures
                atomic_json(self.data_dir / "status.json", {
                    "schema_version": SCHEMA, "generated_at": iso_now(),
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500],
                    "safety": {"public_data_only": True, "orders_allowed": False},
                })
                raise
            remaining = float(record["next_interval_seconds"]) - (
                time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)


def load_rows(paths: Iterable[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def _market_map(match: Mapping[str, Any]) -> dict[str, dict]:
    return {str(row.get("ticker")): dict(row)
            for row in match.get("markets") or [] if row.get("ticker")}


def _trade_price(trade: Mapping[str, Any]) -> float | None:
    value = fnum(trade.get("yes_price_dollars"))
    if value is not None:
        return value
    cents = fnum(trade.get("yes_price"))
    return cents / 100 if cents is not None else None


def analyze(rows: list[dict]) -> dict:
    by_match: dict[str, list[tuple[datetime, dict]]] = {}
    for cycle in rows:
        captured = parse_time(cycle.get("captured_at"))
        if captured is None:
            continue
        for match in cycle.get("matches") or []:
            by_match.setdefault(str(match.get("espn_event_id")), []).append(
                (captured, dict(match)))
    observations = []
    completed_matches = 0
    for match_id, snapshots in sorted(by_match.items()):
        snapshots.sort(key=lambda row: row[0])
        if any(str(((match.get("status") or {}).get("type") or {}).get("state")) == "post"
               for _, match in snapshots):
            completed_matches += 1
        events: dict[str, tuple[datetime, dict]] = {}
        for captured, match in snapshots:
            for event in match.get("key_events") or []:
                if event.get("kind") not in {"goal", "red_card"}:
                    continue
                events.setdefault(str(event.get("id")), (captured, dict(event)))
        for event_id, (first_seen, event) in events.items():
            wall = parse_time(event.get("wallclock"))
            if wall is None or first_seen > wall + timedelta(seconds=75):
                continue
            pre = [row for row in snapshots if timedelta(0) <= wall - row[0] <= timedelta(seconds=30)]
            post = [row for row in snapshots if timedelta(seconds=45) <= row[0] - wall <= timedelta(seconds=75)]
            if not pre or not post:
                continue
            pre_at, pre_match = pre[-1]
            post_at, post_match = post[0]
            pre_markets, post_markets = _market_map(pre_match), _market_map(post_match)
            team_code = str(event.get("team_abbreviation") or "").upper()
            for ticker in sorted(set(pre_markets) & set(post_markets)):
                before, after = pre_markets[ticker], post_markets[ticker]
                b0, b1 = best_book(before), best_book(after)
                if b0 is None or b1 is None:
                    continue
                same_team = str(before.get("side_code") or "").upper() == team_code
                direction = (1 if same_team else -1)
                if event["kind"] == "red_card":
                    direction *= -1
                gross = direction * (b1["mid"] - b0["mid"])
                entry = b0["ask"] if direction > 0 else b0["bid"]
                fee = fee_per_contract(entry, maker=False, series_ticker=SERIES)
                markout = ((b1["mid"] - b0["ask"]) if direction > 0
                           else (b0["bid"] - b1["mid"])) - fee
                capacity = b0["ask_qty"] if direction > 0 else b0["bid_qty"]
                prices = []
                seen_trade_ids = set()
                for captured, snap in snapshots:
                    if not wall <= captured <= wall + timedelta(seconds=75):
                        continue
                    market = _market_map(snap).get(ticker) or {}
                    for trade in market.get("trades") or []:
                        trade_id = str(trade.get("trade_id") or json.dumps(trade, sort_keys=True))
                        if trade_id in seen_trade_ids:
                            continue
                        trade_at = parse_time(trade.get("created_time"))
                        price = _trade_price(trade)
                        if trade_at and wall <= trade_at <= wall + timedelta(seconds=60) and price is not None:
                            seen_trade_ids.add(trade_id)
                            prices.append(price)
                maker_fill = any(price < b0["bid"] for price in prices) if direction > 0 else any(
                    price > b0["ask"] for price in prices)
                observations.append({
                    "match_id": match_id, "event_id": event_id,
                    "event_kind": event["kind"], "ticker": ticker,
                    "direction": direction, "wallclock": event["wallclock"],
                    "pre_at": pre_at.isoformat(), "post_at": post_at.isoformat(),
                    "gross_move": gross, "taker_markout": markout,
                    "stale_60s": gross < 0.01, "displayed_contracts": capacity,
                    "strict_through_maker_fill": maker_fill,
                })
    per_shock: dict[tuple[str, str], list[dict]] = {}
    for row in observations:
        per_shock.setdefault((row["match_id"], row["event_id"]), []).append(row)
    shocks = []
    for (match_id, event_id), values in sorted(per_shock.items()):
        shocks.append({
            "match_id": match_id,
            "event_id": event_id,
            "gross_move": statistics.median(row["gross_move"] for row in values),
            "taker_markout": statistics.fmean(
                row["taker_markout"] for row in values),
            "stale_60s": statistics.median(
                row["gross_move"] for row in values) < .01,
            "strict_through_maker_fill_rate": statistics.fmean(
                row["strict_through_maker_fill"] for row in values),
            "displayed_contracts": statistics.median(
                row["displayed_contracts"] for row in values),
            "market_rows": len(values),
        })
    per_match: dict[str, list[dict]] = {}
    for shock in shocks:
        per_match.setdefault(shock["match_id"], []).append(shock)
    match_means = [statistics.fmean(shock["taker_markout"] for shock in values)
                   for values in per_match.values()]
    ci = [None, None]
    mean = None
    if match_means:
        mean = statistics.fmean(match_means)
        rng = random.Random(BOOTSTRAP_SEED)
        samples = sorted(statistics.fmean(
            match_means[rng.randrange(len(match_means))]
            for _ in match_means) for _ in range(BOOTSTRAP_REPS))
        ci = [samples[int(.025 * BOOTSTRAP_REPS)],
              samples[int(.975 * BOOTSTRAP_REPS)]]
    gross_values = [shock["gross_move"] for shock in shocks]
    stale_rate = (statistics.fmean(shock["stale_60s"] for shock in shocks)
                  if shocks else None)
    fill_rate = (statistics.fmean(
        shock["strict_through_maker_fill_rate"] for shock in shocks)
        if shocks else None)
    capacities = [shock["displayed_contracts"] for shock in shocks]
    enough = completed_matches >= 20 and len(shocks) >= 20 and len(per_match) >= 10
    verdict = "NO DECISION"
    if enough:
        if statistics.median(gross_values) <= .0225 or (stale_rate or 0) < .20:
            verdict = "KILL — prompt incorporation"
        elif (fill_rate or 0) < .02 or statistics.median(capacities) < 5:
            verdict = "KILL — not executable"
        elif (mean is not None and ci[0] is not None and ci[0] > 0 and
              (stale_rate or 0) >= .20 and (fill_rate or 0) >= .10 and
              statistics.median(capacities) >= 25):
            verdict = "PASS TO CAPACITY STUDY ONLY"
    return {
        "schema_version": SCHEMA, "rule_document": RULE,
        "completed_matches": completed_matches, "qualifying_shocks": len(shocks),
        "qualifying_matches": len(per_match), "market_rows": len(observations),
        "median_gross_move": statistics.median(gross_values) if gross_values else None,
        "equal_match_mean_taker_markout": mean, "clustered_95_ci": ci,
        "stale_60s_rate": stale_rate, "strict_through_maker_fill_rate": fill_rate,
        "median_displayed_contracts": statistics.median(capacities) if capacities else None,
        "verdict": verdict, "shocks": shocks, "observations": observations,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=ROOT / "data/laliga_spread_latency")
    parser.add_argument("--interval", type=float, default=10.0)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--analyze", action="store_true")
    args = parser.parse_args(argv)
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    if args.analyze:
        result = analyze(load_rows(sorted((args.data_dir / "snapshots").glob("*.jsonl"))))
        atomic_json(args.data_dir / "analysis.json", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    collector = Collector(args.data_dir, args.interval)
    if args.once:
        print(json.dumps(collector.collect_once(), indent=2, sort_keys=True))
    else:
        collector.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
