#!/usr/bin/env python3
"""Run a read-only ProphetX readiness validation and write a safe JSON report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.collect_crossvenue_basis import (  # noqa: E402
    kalshi_games, match_pairs, prophetx_moneylines, snapshot,
)
from src.kalshi_public import KalshiPublic  # noqa: E402
from src.prophetx_client import ProphetXClient  # noqa: E402
from src.prophetx_readiness import build_readiness_report  # noqa: E402


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def validate(*, environment: str, tax_gate0_resolved: bool = False,
             production_collection_approved: bool = False,
             client=None, kalshi=None) -> dict:
    """Collect one ephemeral sample. No observation rows or shapes are saved."""
    px = client or ProphetXClient(env=environment)
    kp = kalshi or KalshiPublic()
    probe = px.probe()
    if not probe.get("authenticated"):
        return build_readiness_report(
            environment=environment, probe=probe, event_count=0,
            moneyline_count=0, pairs=[], unmatched_reasons={}, snapshot_rows=[],
            tax_gate0_resolved=tax_gate0_resolved,
            production_collection_approved=production_collection_approved)
    events = px.sport_events() or []
    moneylines = prophetx_moneylines(px)
    games = kalshi_games(kp)
    pairs, reasons = match_pairs(games, moneylines)
    for pair in pairs:
        pair["px_environment"] = environment
    rows = [row for pair in pairs for row in snapshot(kp, px, pair, 0.32)]
    return build_readiness_report(
        environment=environment, probe=probe, event_count=len(events),
        moneyline_count=len(moneylines), pairs=pairs,
        unmatched_reasons=reasons, snapshot_rows=rows,
        tax_gate0_resolved=tax_gate0_resolved,
        production_collection_approved=production_collection_approved)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("sandbox", "production"),
                        default=os.getenv("PROPHETX_ENV", "sandbox"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ack-production-read-only", action="store_true",
                        help="required for a production API validation; this "
                             "does not enable collection or execution")
    parser.add_argument("--tax-gate0-resolved", action="store_true")
    parser.add_argument("--production-collection-approved", action="store_true")
    args = parser.parse_args(argv)
    if args.env == "production" and not args.ack_production_read_only:
        parser.error("production validation requires --ack-production-read-only")
    output = args.output or ROOT / "data" / "prophetx_readiness" / f"{args.env}.json"
    report = validate(
        environment=args.env,
        tax_gate0_resolved=args.tax_gate0_resolved,
        production_collection_approved=args.production_collection_approved)
    _write_atomic(output, report)
    print(json.dumps({
        "environment": report["environment"], "status": report["status"],
        "technical_ready": report["technical_ready"],
        "rollout_ready": report["rollout_ready"],
        "blockers": report["blockers"], "output": str(output),
    }, sort_keys=True))
    return 0 if report["technical_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
