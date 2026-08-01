#!/usr/bin/env python3
"""
scripts/strategy_agent_submit.py
────────────────────────────────
Write a strategy request into the live agent queue.

This is the simplest operational entrypoint for the recursive strategy loop:

    python3 -m scripts.strategy_agent_submit --type opportunity --payload-file ...

The runner consumes the request from data/strategy_agents/queue/<role>/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.strategy_agent_runtime import ROLE_BY_TYPE  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--type", required=True,
                    choices=sorted(ROLE_BY_TYPE.keys()))
    ap.add_argument("--strategy-id", default=None)
    ap.add_argument("--payload-file", default=None)
    ap.add_argument("--payload-json", default=None,
                    help="raw JSON object; defaults to stdin if omitted")
    ap.add_argument("--queue-dir", default="data/strategy_agents/queue")
    args = ap.parse_args()

    if args.payload_file and args.payload_json:
        raise SystemExit("use either --payload-file or --payload-json, not both")

    if args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text())
    elif args.payload_json:
        payload = json.loads(args.payload_json)
    else:
        payload = json.loads(sys.stdin.read())

    queue_dir = Path(args.queue_dir)
    role = ROLE_BY_TYPE[args.type]
    target_dir = queue_dir / role
    target_dir.mkdir(parents=True, exist_ok=True)

    strategy_id = args.strategy_id or payload.get("id") or payload.get("strategy_id") or "unknown"
    request = {
        "type": args.type,
        "strategy_id": strategy_id,
        "actor": role,
        "submitted_at": _utc_now(),
        "payload": payload,
    }

    stamp = _utc_now().replace(":", "").replace("-", "")
    out = target_dir / f"{stamp}_{_sanitize(strategy_id)}_{args.type}.json"
    serialized = json.dumps(request, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(target_dir), prefix=".submit-",
            suffix=".tmp", encoding="utf-8") as fh:
        fh.write(serialized)
        fh.flush()
        os.fsync(fh.fileno())
        tmp_path = Path(fh.name)
    os.replace(tmp_path, out)
    out.chmod(0o600)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
