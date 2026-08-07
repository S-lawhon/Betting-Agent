#!/usr/bin/env python3
"""Run the sanctioned P-029 reader remotely and persist its result atomically."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.p029_gate0c_checkpoint import (  # noqa: E402
    EXTENSION_READ_NOT_BEFORE,
    KNOWN_TAPE_GAPS,
    READ_NOT_BEFORE,
)

UTC = timezone.utc
DEFAULT_OUTPUT_DIR = ROOT / "data" / "p029_gate0c"
DEFAULT_HOST = "root@143.198.162.120"
DEFAULT_KEY = "/root/.ssh/p029_probe"
REMOTE_READER = "/opt/p029/scripts/p029_gate0c_checkpoint.py"
REMOTE_PYTHON = "/opt/p029/venv/bin/python3"
TERMINAL_VERDICTS = {"CONTINUE", "STOP"}


class CheckpointError(RuntimeError):
    pass


def _parse_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _persist(output_dir: Path, payload: Mapping[str, Any], at: datetime) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    immutable = output_dir / f"checkpoint_{stamp}.json"
    suffix = 1
    while immutable.exists():
        immutable = output_dir / f"checkpoint_{stamp}_{suffix}.json"
        suffix += 1
    _write_atomic(immutable, payload)
    _write_atomic(output_dir / "latest.json", payload)
    return immutable


def _remote_result(host: str, key: Path, timeout_seconds: int,
                   now_override: Optional[str] = None) -> Dict[str, Any]:
    if not key.is_file():
        raise CheckpointError(f"P-029 probe key is missing: {key}")
    remote = [REMOTE_PYTHON, REMOTE_READER, "--json"]
    if now_override:
        remote.extend(["--now", now_override])
    command = [
        "ssh", "-i", str(key), "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host,
        " ".join(remote),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckpointError(
            f"remote Gate 0c reader timed out after {timeout_seconds}s") from exc
    if completed.returncode != 0:
        raise CheckpointError(
            f"remote Gate 0c reader exited {completed.returncode}; "
            f"stderr_sha256={hashlib.sha256(completed.stderr.encode()).hexdigest()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError("remote Gate 0c reader returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("pod") != "P-029":
        raise CheckpointError("remote Gate 0c reader returned the wrong payload")
    return payload


def qualify(payload: Mapping[str, Any], retrieved_at: datetime,
            host: str) -> Dict[str, Any]:
    result = dict(payload)
    qualification = dict(result.get("data_qualification") or {})
    qualification["known_tape_gaps"] = KNOWN_TAPE_GAPS
    qualification["collection_complete"] = False
    result["data_qualification"] = qualification
    result["retrieved_at_utc"] = retrieved_at.astimezone(UTC).isoformat().replace(
        "+00:00", "Z")
    result["source_host"] = host
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("P029_GATE0C_HOST", DEFAULT_HOST))
    parser.add_argument("--key", type=Path,
                        default=Path(os.getenv("P029_GATE0C_SSH_KEY", DEFAULT_KEY)))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout-seconds", type=int, default=21_600)
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    at = _parse_time(args.now)

    if at < READ_NOT_BEFORE:
        print("P-029 Gate 0c remains blind before 2026-08-23", file=sys.stderr)
        return 2
    previous = _load(args.output_dir / "latest.json")
    if (at >= EXTENSION_READ_NOT_BEFORE and previous
            and previous.get("verdict") in TERMINAL_VERDICTS):
        print("P-029 Gate 0c already has terminal verdict {}; extension skipped".format(
            previous["verdict"]))
        return 0

    try:
        payload = _remote_result(
            args.host, args.key, args.timeout_seconds, now_override=args.now)
        if payload.get("verdict") == "UNAVAILABLE":
            raise CheckpointError(str(payload.get("reason") or "reader unavailable"))
        qualified = qualify(payload, at, args.host)
        path = _persist(args.output_dir, qualified, at)
    except (CheckpointError, OSError, ValueError) as exc:
        print(f"P-029 Gate 0c checkpoint failed: {exc}", file=sys.stderr)
        return 1

    print(f"P-029 Gate 0c checkpoint saved: {path}")
    print(json.dumps(qualified, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
