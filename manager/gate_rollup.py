#!/usr/bin/env python3
"""Build the compact sanctioned-gate artifact consumed by manager snapshots."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from manager.collect import Collector, jsonable


UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
GATE_READERS = (
    ("p015", "p015_gate"),
    ("p017", "p017_gate"),
    ("p001", "p001_gate"),
    ("p022", "p022_gate"),
    ("p014", "p014_gate"),
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=jsonable) + "\n",
        encoding="utf-8")
    os.replace(temporary, path)


def build(root: Path, registry_path: Path, *,
          generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    generated_at = (generated_at or datetime.now(UTC)).astimezone(UTC)
    collector = Collector(registry_path, root=root)
    blocks: Dict[str, Any] = {}
    for key, method_name in GATE_READERS:
        blocks[key] = getattr(collector, method_name)() or {
            "id": key.upper(), "available": False,
            "error": "sanctioned reader returned no result",
        }
    throughput = collector.throughput(blocks) or {
        "available": False, "error": "throughput reader returned no result",
    }
    faults = list(collector.faults)
    unavailable = [
        key for key, value in {**blocks, "throughput": throughput}.items()
        if not isinstance(value, dict) or value.get("available") is False
    ]
    return {
        "schema_version": 1,
        "generated_at": _iso(generated_at),
        "status": "degraded" if faults or unavailable else "healthy",
        **blocks,
        "throughput": throughput,
        "faults": faults,
        "unavailable": unavailable,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    registry = args.registry or args.root / "manager" / "registry.yaml"
    output = args.output or args.root / "manager" / "state" / "gate_rollup.json"
    payload = build(args.root, registry)
    _write_atomic(output, payload)
    print("manager gate rollup: status={} faults={} -> {}".format(
        payload["status"], len(payload["faults"]), output))
    return 0 if payload["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
