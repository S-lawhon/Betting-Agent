#!/usr/bin/env python3
"""Claim, complete, release, or inspect bounded research work."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research_execution import (  # noqa: E402
    ResearchExecutionError,
    claim_next,
    complete_claim,
    execution_status,
    release_claim,
)


ROOT = Path(__file__).resolve().parent.parent


def _now(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ResearchExecutionError("expected a JSON object in {}".format(path))
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatches-dir", type=Path,
                        default=ROOT / "data/research_triage/dispatches")
    parser.add_argument("--state-dir", type=Path,
                        default=ROOT / "data/research_execution")
    parser.add_argument("--dispositions-dir", type=Path,
                        default=ROOT / "research/dispositions")
    parser.add_argument("--now", help="ISO timestamp (test/replay only)")
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim")
    claim.add_argument("--worker-id", required=True)
    claim.add_argument("--agent", choices=(
        "literature-scout", "social-scout", "strategy-scout"))
    claim.add_argument("--lease-minutes", type=int, default=90)

    complete = sub.add_parser("complete")
    complete.add_argument("--claim", type=Path, required=True)
    complete.add_argument("--disposition", type=Path, required=True)

    release = sub.add_parser("release")
    release.add_argument("--claim", type=Path, required=True)
    release.add_argument("--reason", required=True)

    sub.add_parser("status")
    args = parser.parse_args(argv)
    now = _now(args.now)
    try:
        if args.command == "claim":
            result = claim_next(
                dispatches_dir=args.dispatches_dir, state_dir=args.state_dir,
                dispositions_dir=args.dispositions_dir,
                worker_id=args.worker_id, agent=args.agent, now=now,
                lease_minutes=args.lease_minutes)
            print(json.dumps({
                "claimed": bool(result),
                "claim": result.to_dict() if result else None,
            }, indent=2, sort_keys=True))
        elif args.command == "complete":
            result = complete_claim(
                claim_path=args.claim,
                disposition_payload=_json(args.disposition),
                state_dir=args.state_dir,
                dispositions_dir=args.dispositions_dir, now=now)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        elif args.command == "release":
            result = release_claim(
                claim_path=args.claim, state_dir=args.state_dir,
                reason=args.reason, now=now)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(json.dumps(execution_status(args.state_dir, now=now),
                             indent=2, sort_keys=True))
    except (OSError, json.JSONDecodeError, ResearchExecutionError,
            TypeError, ValueError) as exc:
        print("research execution error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
