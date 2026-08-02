#!/usr/bin/env python3
"""Cache MLB outcomes and estimate observed settlement-basis incidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dislocation_cases import CaseLedger  # noqa: E402
from src.mlb_statsapi import MlbStatsApi  # noqa: E402
from src.settlement_basis_evidence import classify_game, summarize_evidence  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def update(*, database: Path, cache_dir: Path, output: Path, pair_id: str,
           client=None, now=None):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    ledger = CaseLedger(database)
    try:
        cases = ledger.cases(pair_id)
    finally:
        ledger.close()
    dates = sorted({str(row.get("match_key") or "").split("|", 1)[0]
                    for row in cases if row.get("match_key")})
    client = client or MlbStatsApi()
    all_games, errors = [], []
    for date_et in dates:
        cache_path = cache_dir / f"{date_et}.json"
        cached = None
        try:
            cached = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        if cached and cached.get("terminal"):
            rows = cached.get("games") or []
        else:
            try:
                games = client.schedule(date_et)
            except Exception as exc:  # keep the daily report alive on API failure
                errors.append(f"{date_et}: schedule fetch failed: {exc}")
                all_games.extend((cached or {}).get("games") or [])
                continue
            rows = [classify_game(game, date_et) for game in games]
            relevant = [row for row in rows
                        if row.get("match_key") in {c.get("match_key") for c in cases}]
            if not relevant:
                errors.append(f"{date_et}: no matching schedule games")
                rows = (cached or {}).get("games") or []
            else:
                rows = relevant
                _atomic(cache_path, {
                    "generated_at": now.isoformat().replace("+00:00", "Z"),
                    "terminal": all(row.get("terminal") for row in rows),
                    "games": rows,
                })
        all_games.extend(rows)
    result = summarize_evidence(cases, all_games)
    result.update({
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "status": "degraded" if errors else "healthy", "errors": errors,
        "pair_id": pair_id,
    })
    _atomic(output, result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "research_cases.sqlite3")
    parser.add_argument("--cache-dir", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "settlement_evidence")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "settlement_basis_evidence.json")
    parser.add_argument("--pair-id", default="gemini_kalshi_mlb_moneyline")
    args = parser.parse_args(argv)
    result = update(database=args.database, cache_dir=args.cache_dir,
                    output=args.output, pair_id=args.pair_id)
    print("settlement evidence: status={} cases={} matched={} exceptions={}".format(
        result["status"], result["case_count"],
        result["cases_matched_to_schedule"], result["observed_exception_cases"]))
    return 0 if result["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
