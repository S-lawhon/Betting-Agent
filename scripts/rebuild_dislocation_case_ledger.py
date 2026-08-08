#!/usr/bin/env python3
"""Rebuild the durable cross-venue research-case ledger from raw JSONL tape."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dislocation_analytics import iter_observations  # noqa: E402
from src.dislocation_cases import (  # noqa: E402
    CaseLedger, build_cases, calibrate_thresholds, settlement_basis,
)
from src.settlement_registry import load_policy_state  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _parse_now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def rebuild(*, observations_dir: Path, database: Path, output: Path,
            settlement_config: Path, settlement_manifest: Path,
            pair_id: str, policy_id: str, now: datetime,
            window_days: int, threshold_usd: float) -> Dict[str, Any]:
    policy = load_policy_state(settlement_config, settlement_manifest, policy_id)
    cases = build_cases(
        iter_observations(observations_dir, now=now, window_days=window_days),
        pair_id=pair_id, policy=policy, now=now,
        threshold_usd=threshold_usd)
    ledger = CaseLedger(database)
    try:
        ledger.upsert(cases, updated_at=now)
        metrics = ledger.summary(pair_id, now=now)
    finally:
        ledger.close()
    metrics.update({
        "generated_at": _iso(now), "window_days_replayed": window_days,
        "threshold_usd": threshold_usd,
        "observations_replayed": sum(
            1 for _ in iter_observations(
                observations_dir, now=now, window_days=window_days)),
        "cases_replayed": len(cases), "settlement_basis": settlement_basis(policy),
        "threshold_calibration": calibrate_thresholds(
            iter_observations(
                observations_dir, now=now, window_days=window_days),
            pair_id=pair_id, policy=policy, now=now),
        "source_tape": str(observations_dir), "ledger": str(database),
    })
    _atomic_json(output, metrics)
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations-dir", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue" / "observations")
    parser.add_argument("--database", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "research_cases.sqlite3")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data" / "gemini_crossvenue"
                        / "research_cases.json")
    parser.add_argument("--settlement-config", type=Path,
                        default=ROOT / "config" / "settlement_equivalence.yaml")
    parser.add_argument("--settlement-manifest", type=Path,
                        default=ROOT / "data" / "settlement_registry" / "manifest.json")
    parser.add_argument("--pair-id", default="gemini_kalshi_mlb_moneyline")
    parser.add_argument("--policy-id", default="gemini_kalshi_mlb_moneyline")
    parser.add_argument("--window-days", type=int, default=36500,
                        help="Replay horizon; default covers the complete retained tape")
    parser.add_argument("--threshold-usd", type=float, default=.03)
    parser.add_argument("--now", help="ISO timestamp for deterministic replay")
    args = parser.parse_args(argv)
    metrics = rebuild(
        observations_dir=args.observations_dir, database=args.database,
        output=args.output, settlement_config=args.settlement_config,
        settlement_manifest=args.settlement_manifest, pair_id=args.pair_id,
        policy_id=args.policy_id, now=_parse_now(args.now),
        window_days=args.window_days, threshold_usd=args.threshold_usd)
    print("case ledger: observations={} replayed_cases={} lifetime_cases={}".format(
        metrics["observations_replayed"], metrics["cases_replayed"],
        metrics["lifetime_cases"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
