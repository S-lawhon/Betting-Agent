#!/usr/bin/env python3
"""Correct legacy P-015 settlements that booked gross instead of net P&L.

The P-015 settler originally omitted the taker fee from ``pnl_usd`` even
though the pod's entry filter and locked gate statistic are fee-net. This tool
is deliberately narrow and dry-run by default. It only rewrites terminal
P-015 rows produced by ``resolution_source=kalshi_api`` that lack both the
gross/fee audit fields and the v2 accounting marker.

Stop ``betting-pod-shop`` before applying so its active JSONL writer cannot
append during an atomic rewrite. Every changed file is backed up first; all
non-target lines are preserved byte-for-byte.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.kalshi_fees import fee_per_contract
from src.kalshi_tennis_settler import _calc_outcome_pnl


POD_ID = "P-015"
BUGGY_SOURCE = "kalshi_api"
VERSION = "p015_net_v2"
DEFAULT_PATTERNS = (
    "data/trade_logs/trade_log*.jsonl",
    "data/trade_logs/trade_log*.jsonl.gz",
    "data/trade_logs/archive/*.jsonl",
    "data/trade_logs/archive/*.jsonl.gz",
)
DEFAULT_ROLLUP_CORRECTIONS = Path(
    "data/corrections/dashboard_pnl_corrections.jsonl"
)


class CorrectionError(ValueError):
    """A target row cannot be corrected without inventing an input."""


@dataclass
class FilePlan:
    path: Path
    lines: List[str]
    corrected: int
    fees_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CorrectionError(f"missing or invalid {label}: {value!r}") from exc
    return result


def is_buggy(rec: Dict[str, Any]) -> bool:
    """Exact legacy signature; unrelated or already-corrected rows are inert."""
    outcome = str(rec.get("outcome") or rec.get("action") or "").upper()
    return (
        rec.get("pod_id") == POD_ID
        and outcome in ("WIN", "LOSS")
        # The first five P-015 settlements predate resolution_source. They are
        # eligible only provisionally: correct_record still has to reproduce
        # their outcome and gross P&L and find stored contract/fee inputs.
        and str(rec.get("resolution_source") or "") in ("", BUGGY_SOURCE)
        and rec.get("fee_accounting_version") != VERSION
        and "fees_usd" not in rec
        and "pnl_gross_usd" not in rec
    )


def correct_record(rec: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[dict]]:
    """Return a corrected copy and audit metadata, or the original and None."""
    if not is_buggy(rec):
        return rec, None

    extra = rec.get("extra")
    if not isinstance(extra, dict):
        raise CorrectionError("target row has no extra mapping")

    side = str(rec.get("side") or "YES")
    result = str(rec.get("result") or "").lower()
    size_usd = _number(rec.get("position_size_usd"), "position_size_usd")
    fill_price = _number(
        rec.get("fill_price") if rec.get("fill_price") is not None
        else rec.get("venue_prob"),
        "fill_price",
    )
    booked_gross = _number(rec.get("pnl_usd"), "pnl_usd")
    expected_outcome, expected_gross = _calc_outcome_pnl(
        side, result, size_usd, fill_price
    )
    booked_outcome = str(rec.get("outcome") or rec.get("action") or "").upper()
    if expected_outcome != booked_outcome:
        raise CorrectionError(
            f"outcome mismatch: booked={booked_outcome} recomputed={expected_outcome}"
        )
    if abs(expected_gross - booked_gross) > 0.011:
        raise CorrectionError(
            f"gross P&L mismatch: booked={booked_gross} recomputed={expected_gross}"
        )

    contracts = _number(
        extra.get("contracts")
        if extra.get("contracts") is not None else rec.get("contracts"),
        "contracts",
    )
    if contracts <= 0:
        raise CorrectionError(f"contracts must be positive: {contracts}")
    stored_fee = extra.get("taker_fee")
    fee_per_ct = (
        _number(stored_fee, "extra.taker_fee")
        if stored_fee is not None else fee_per_contract(fill_price)
    )
    if fee_per_ct < 0:
        raise CorrectionError(f"taker fee must be non-negative: {fee_per_ct}")

    fees_usd = round(contracts * fee_per_ct, 4)
    pnl_net = round(booked_gross - fees_usd, 2)
    fixed = dict(rec)
    fixed.update({
        "pnl_usd": pnl_net,
        "pnl_gross_usd": booked_gross,
        "fees_usd": fees_usd,
        "fee_accounting_version": VERSION,
        "fee_correction_note": (
            "Corrected 2026-08-07: legacy P-015 settlement booked gross P&L; "
            "pnl_usd is now net of the stored placement taker fee."
        ),
    })
    return fixed, {
        "fees_usd": fees_usd,
        "gross_pnl_usd": booked_gross,
        "net_pnl_usd": pnl_net,
    }


def discover_files(patterns: Iterable[str]) -> List[Path]:
    found = set()
    for pattern in patterns:
        for raw in glob.glob(str(pattern)):
            path = Path(raw)
            if path.is_file():
                found.add(path)
    return sorted(found)


def _read_lines(path: Path) -> List[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


def plan_file(path: Path) -> FilePlan:
    out: List[str] = []
    corrected = 0
    fees = gross = net = 0.0
    for line_no, raw in enumerate(_read_lines(path), 1):
        stripped = raw.rstrip("\r\n")
        ending = raw[len(stripped):]
        if not stripped.strip():
            out.append(raw)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            out.append(raw)
            continue
        try:
            fixed, audit = correct_record(rec)
        except CorrectionError as exc:
            raise CorrectionError(f"{path}:{line_no}: {exc}") from exc
        if audit is None:
            out.append(raw)
            continue
        out.append(json.dumps(fixed, default=str) + (ending or "\n"))
        corrected += 1
        fees += audit["fees_usd"]
        gross += audit["gross_pnl_usd"]
        net += audit["net_pnl_usd"]
    return FilePlan(path, out, corrected, fees, gross, net)


def _write_plan(plan: FilePlan, stamp: str) -> Path:
    path = plan.path
    backup = Path(str(path) + f".bak_p015_fee_{stamp}")
    if backup.exists():
        raise CorrectionError(f"backup already exists: {backup}")
    shutil.copy2(path, backup)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=path.suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(tmp, "wt", encoding="utf-8") as fh:
            fh.writelines(plan.lines)
            fh.flush()
            if path.suffix != ".gz":
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return backup


def summary(plans: Iterable[FilePlan]) -> dict:
    plans = list(plans)
    changed = [p for p in plans if p.corrected]
    return {
        "files_scanned": len(plans),
        "files_changed": len(changed),
        "rows_corrected": sum(p.corrected for p in changed),
        "gross_pnl_usd": round(sum(p.gross_pnl_usd for p in changed), 2),
        "fees_usd": round(sum(p.fees_usd for p in changed), 4),
        "net_pnl_usd": round(sum(p.net_pnl_usd for p in changed), 2),
        "pnl_delta_usd": round(-sum(p.fees_usd for p in changed), 4),
        "paths_changed": [str(p.path) for p in changed],
    }


def settlement_inventory(files: Iterable[Path]) -> dict:
    """Classify deduplicated P-015 terminal rows without exposing trade data."""
    settled: Dict[str, Dict[str, Any]] = {}
    for path in files:
        for raw in _read_lines(path):
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            outcome = str(rec.get("outcome") or rec.get("action") or "").upper()
            if rec.get("pod_id") != POD_ID or outcome not in ("WIN", "LOSS"):
                continue
            key = str(
                rec.get("fingerprint")
                or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}"
            )
            settled[key] = rec

    groups: collections.Counter = collections.Counter()
    for rec in settled.values():
        groups[(
            str(rec.get("resolution_source") or "(missing)"),
            str(rec.get("fee_accounting_version") or "(missing)"),
            "present" if "fees_usd" in rec else "missing",
            "present" if "pnl_gross_usd" in rec else "missing",
        )] += 1
    return {
        "unique_terminal_rows": len(settled),
        "groups": [
            {
                "resolution_source": key[0],
                "fee_accounting_version": key[1],
                "fees_usd": key[2],
                "pnl_gross_usd": key[3],
                "rows": count,
            }
            for key, count in sorted(groups.items())
        ],
    }


def rollup_correction_rows(files: Iterable[Path]) -> List[Dict[str, Any]]:
    """Dollar deltas by settlement day from corrected, deduplicated rows."""
    settled: Dict[str, Dict[str, Any]] = {}
    for path in files:
        for raw in _read_lines(path):
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            outcome = str(rec.get("outcome") or rec.get("action") or "").upper()
            if (rec.get("pod_id") != POD_ID
                    or outcome not in ("WIN", "LOSS")
                    or rec.get("fee_accounting_version") != VERSION):
                continue
            key = str(
                rec.get("fingerprint")
                or f"{rec.get('market_id')}|{rec.get('settled_at_utc')}"
            )
            settled[key] = rec

    by_day: Dict[str, float] = collections.defaultdict(float)
    for rec in settled.values():
        day = str(rec.get("settled_at_utc") or "")[:10]
        if len(day) != 10:
            raise CorrectionError("corrected row has no settlement day")
        by_day[day] -= _number(rec.get("fees_usd"), "fees_usd")
    return [
        {
            "correction_id": f"p015_fee_net_v2:{day}",
            "pod_id": POD_ID,
            "day": day,
            "pnl_delta_usd": round(delta, 4),
            "note": (
                "P-015 legacy settlements booked gross P&L; subtract stored "
                "taker fees. See REPORT_P015_US_Open_Readiness_2026-08-07.md."
            ),
        }
        for day, delta in sorted(by_day.items())
    ]


def write_rollup_corrections(path: Path, rows: Iterable[Dict[str, Any]],
                             stamp: str) -> Dict[str, Any]:
    """Append new correction IDs atomically; matching IDs are idempotent."""
    rows = list(rows)
    existing_lines: List[str] = []
    existing: Dict[str, Dict[str, Any]] = {}
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines(True)
        for line_no, raw in enumerate(existing_lines, 1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CorrectionError(
                    f"invalid rollup correction JSON at {path}:{line_no}"
                ) from exc
            cid = str(rec.get("correction_id") or "")
            if cid:
                existing[cid] = rec

    additions = []
    for row in rows:
        cid = str(row.get("correction_id") or "")
        prior = existing.get(cid)
        if prior is not None:
            comparable = ("pod_id", "day", "pnl_delta_usd")
            if any(prior.get(k) != row.get(k) for k in comparable):
                raise CorrectionError(f"conflicting rollup correction id {cid!r}")
            continue
        additions.append(row)

    backup = None
    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = Path(str(path) + f".bak_{stamp}")
            shutil.copy2(path, backup)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(existing_lines)
                if existing_lines and not existing_lines[-1].endswith("\n"):
                    fh.write("\n")
                for row in additions:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    return {
        "path": str(path),
        "rows_derived": len(rows),
        "rows_added": len(additions),
        "pnl_delta_usd": round(sum(float(r["pnl_delta_usd"])
                                   for r in rows), 4),
        "backup": str(backup) if backup else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append", default=[],
                    help="path or glob; repeatable (defaults to active + archives)")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite matching rows; default is dry-run")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--write-rollup-corrections", action="store_true",
        help="with --apply, atomically record per-day dashboard P&L deltas",
    )
    ap.add_argument("--rollup-corrections", type=Path,
                    default=DEFAULT_ROLLUP_CORRECTIONS)
    args = ap.parse_args()

    files = discover_files(args.log or DEFAULT_PATTERNS)
    try:
        plans = [plan_file(path) for path in files]
    except CorrectionError as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 3
    result = summary(plans)
    result["settlement_inventory"] = settlement_inventory(files)
    result["mode"] = "apply" if args.apply else "dry-run"

    if args.apply and result["rows_corrected"]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            result["backups"] = [
                str(_write_plan(plan, stamp))
                for plan in plans if plan.corrected
            ]
        except (OSError, CorrectionError) as exc:
            print(f"APPLY FAILED: {exc}", file=sys.stderr)
            return 4

    if args.write_rollup_corrections:
        if not args.apply:
            print("REFUSING: --write-rollup-corrections requires --apply",
                  file=sys.stderr)
            return 5
        try:
            result["rollup_corrections"] = write_rollup_corrections(
                args.rollup_corrections,
                rollup_correction_rows(files),
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            )
        except (OSError, CorrectionError) as exc:
            print(f"ROLLUP CORRECTION FAILED: {exc}", file=sys.stderr)
            return 6

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
        if not args.apply:
            print("[dry-run] nothing written; stop the service and add --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
