#!/usr/bin/env python3
"""
scripts/rotate_active_log.py
────────────────────────────
Size-capped rotation of ``data/trade_logs/trade_log.jsonl`` that CARRIES
FORWARD still-open positions.

Why this exists
───────────────
The droplet has been running a shell rotator (``scripts/rotate_active_log.sh``,
cron ``0 6 * * *``) that gzips the active log and then truncates it::

    gzip -c "$LOG" > "${LOG%.jsonl}.archive_${TS}.jsonl.gz"
    : > "$LOG"

``TradeStore.load()`` reads ONLY the active log.  So every PLACED row that has
not settled by rotation time is erased from the store's view at the next
process restart, and:

- ``store.get_open_trades(pod)`` returns nothing for those positions;
- every settler that sources positions from the store (golf, tennis) silently
  no-ops — ``KalshiGolfSettler.settle_cycle()`` returns at its
  ``if not open_entries`` guard and logs only at DEBUG;
- the settler's own stale-timeout backstop never fires either, because it lives
  inside the per-market loop that is never entered;
- ``AggregateRiskGuard`` bootstraps with the exposure missing;
- the pod's open-position counter resets, so it will re-enter a full book.

Observed in production 2026-07-26: P-017 placed 38 rows for the 3M Open on
2026-07-21; the log rotated 2026-07-22 06:00; the service restarted
2026-07-25 15:05 and loaded 22 placed / 5 open.  The 16 P-017 positions still
live at that moment ($146.96) became unsettleable.  This is a silent failure —
nothing errors, the settler simply has nothing to look at.

Long-dated pods are the exposed ones: golf top-N opens 4-10 days before close,
tennis qualifiers days ahead, and P-001 holds NFL futures months out.  The
active log holds ~1.5 days of traffic before it trips the 50 MB cap.

What this does instead
──────────────────────
1. Gzip the active log to ``trade_log.archive_<ts>.jsonl.gz`` (unchanged).
2. Rewrite the active log containing ONLY the still-open PLACED rows, verbatim.
3. Prune to the 12 most recent archives (unchanged).

Carrying the rows verbatim (same ``fingerprint``) is what makes this safe:
``TradeStore._index_entry`` keys ``_placed`` by fingerprint and settlement
records are written as ``{**placed_entry, "action": outcome}``, so a settlement
appended after the carry-forward still pairs with it.  Re-appending a row the
store has already seen is idempotent for the same reason.

Usage
─────
    # replaces the cron entry for rotate_active_log.sh
    python3 -m scripts.rotate_active_log

    # one-off: pull unresolved PLACED rows back out of recent archives and
    # re-append them to the active log (repairs an already-orphaned book).
    # ALWAYS scope with --pod: a blanket recovery against the 2026-07-26
    # droplet state would also resurrect 161 rows ($1311) from P-002/P-006,
    # which have no settler and would therefore pin that exposure in the
    # risk guard forever.
    python3 -m scripts.rotate_active_log --recover-open-from-archives 3 --pod P-017

    # inspect without writing
    python3 -m scripts.rotate_active_log --dry-run

NOTE: this script is NOT deployed.  Installing it on the droplet (and
repointing the cron entry) is a deliberate operational change.
"""

import argparse
import gzip
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/trade_logs/trade_log.jsonl")
DEFAULT_CAP_BYTES = 50 * 1024 * 1024
KEEP_ARCHIVES = 12

SETTLEMENT_OUTCOMES = ("WIN", "LOSS", "VOID")

# Cheap pre-filter.  The active log is >99% SKIPPED_* rows; JSON-parsing all of
# them to find a few hundred PLACED rows is the whole cost of this script.
# Every row we care about carries one of these substrings.
_INTERESTING = ('"PLACED"', '"WIN"', '"LOSS"', '"VOID"', '"BANKROLL_RESET"')


def _is_interesting(raw: str) -> bool:
    return any(tok in raw for tok in _INTERESTING)


def open_placed_rows(line_sources: Iterable[Iterable[str]]) -> Dict[str, str]:
    """Return ``{fingerprint: raw_json_line}`` for PLACED rows with no settlement.

    ``line_sources`` is an iterable of line iterables, supplied in CHRONOLOGICAL
    order (oldest archive first, active log last).  Pairing is by fingerprint,
    matching ``TradeStore._index_entry``: an entry is a placement when
    ``action == "PLACED"`` and a settlement when ``outcome`` (or ``action``, for
    the settlers that only set the one field) is WIN/LOSS/VOID.

    ``BANKROLL_RESET`` clears the book, mirroring the settlers' file-fallback
    reader — a reset means the prior positions are no longer being tracked.
    """
    placed: Dict[str, str] = {}
    settled: set = set()

    for lines in line_sources:
        for raw in lines:
            raw = raw.strip()
            if not raw or not _is_interesting(raw):
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue

            action = str(rec.get("action") or "").upper()
            outcome = str(rec.get("outcome") or "").upper()

            if action == "BANKROLL_RESET":
                placed.clear()
                settled.clear()
                continue

            fp = rec.get("fingerprint") or ""
            if not fp:
                # Without a fingerprint the store cannot index it as open
                # either, so carrying it forward would not help.
                continue

            if outcome in SETTLEMENT_OUTCOMES or action in SETTLEMENT_OUTCOMES:
                settled.add(fp)
                placed.pop(fp, None)
                continue

            if action == "PLACED" and float(rec.get("position_size_usd") or 0) > 0:
                if fp not in settled:
                    placed[fp] = raw

    return placed


def _read_lines(path: Path) -> Iterable[str]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore[operator]
        for line in fh:
            yield line


def recent_archives(log_path: Path, count: int) -> List[Path]:
    """The *count* most recent ``trade_log.archive_*.jsonl.gz`` files, oldest first."""
    stem = log_path.name[: -len(".jsonl")] if log_path.name.endswith(".jsonl") else log_path.stem
    found = sorted(
        log_path.parent.glob(f"{stem}.archive_*.jsonl.gz"),
        key=lambda p: p.stat().st_mtime,
    )
    return found[-count:] if count > 0 else []


def _atomic_write(path: Path, payload: str) -> None:
    """Write *payload* to *path* atomically, preserving ownership and mode."""
    try:
        st = path.stat()
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode
    except OSError:
        uid = gid = -1
        mode = 0o644

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".rotate_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        if uid != -1:
            try:
                os.chown(tmp_name, uid, gid)
            except PermissionError:
                logger.warning("could not restore ownership on %s", path)
        os.chmod(tmp_name, mode & 0o777)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _prune_archives(log_path: Path, keep: int = KEEP_ARCHIVES) -> List[Path]:
    archives = recent_archives(log_path, count=10_000)
    doomed = archives[: max(0, len(archives) - keep)]
    for p in doomed:
        p.unlink()
    return doomed


def rotate(
    log_path: Path = DEFAULT_LOG_PATH,
    cap_bytes: int = DEFAULT_CAP_BYTES,
    dry_run: bool = False,
    _now_fn=None,
) -> dict:
    """Archive the active log and rewrite it holding only still-open positions."""
    now = (_now_fn or (lambda: datetime.now(timezone.utc)))()
    result = {"rotated": False, "size_bytes": 0, "carried_open": 0, "archive": None}

    if not log_path.exists():
        logger.info("rotate: no active log at %s", log_path)
        return result

    size = log_path.stat().st_size
    result["size_bytes"] = size
    if size <= cap_bytes:
        logger.info("rotate: %s is %d bytes, under the %d cap — nothing to do", log_path, size, cap_bytes)
        return result

    carried = open_placed_rows([_read_lines(log_path)])
    payload = "".join(raw + "\n" for raw in carried.values())
    result["carried_open"] = len(carried)

    archive = log_path.with_name(
        f"{log_path.name[:-len('.jsonl')]}.archive_{now.strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
    )
    result["archive"] = str(archive)

    if dry_run:
        logger.info(
            "rotate [dry-run]: would archive %s (%d bytes) -> %s and carry %d open position(s)",
            log_path, size, archive, len(carried),
        )
        return result

    with open(log_path, "rb") as src, gzip.open(archive, "wb") as dst:
        shutil.copyfileobj(src, dst)

    _atomic_write(log_path, payload)
    pruned = _prune_archives(log_path)
    result["rotated"] = True
    result["pruned"] = [str(p) for p in pruned]
    logger.info(
        "rotate: archived %d bytes -> %s; carried %d open position(s) forward; pruned %d archive(s)",
        size, archive, len(carried), len(pruned),
    )
    return result


def recover_open_from_archives(
    log_path: Path = DEFAULT_LOG_PATH,
    archive_count: int = 3,
    dry_run: bool = False,
    pods: Optional[Iterable[str]] = None,
) -> dict:
    """Re-append unresolved PLACED rows that only survive in recent archives.

    Repairs a book that a truncating rotation already orphaned.  Rows already
    present in the active log are not duplicated (same fingerprint wins), and
    anything settled anywhere in the scanned window is excluded.

    ``pods`` scopes the recovery, and you almost always want it.  Recovering a
    position for a pod that has no settler re-injects exposure that will never
    drain — ``AggregateRiskGuard`` deliberately skips those pods at bootstrap
    for exactly this reason (see CLAUDE.md, ``settled_pod_ids``).  Measured on
    the droplet 2026-07-26: an unscoped recovery would restore 177 rows, of
    which only the 16 P-017 rows belong to a pod that can settle.
    """
    pod_filter = set(pods) if pods else None
    archives = recent_archives(log_path, archive_count)
    sources: List[Iterable[str]] = [_read_lines(p) for p in archives]
    if log_path.exists():
        sources.append(_read_lines(log_path))

    carried = open_placed_rows(sources)

    already: set = set()
    if log_path.exists():
        for raw in _read_lines(log_path):
            if '"PLACED"' not in raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            fp = rec.get("fingerprint")
            if fp:
                already.add(fp)

    missing: Dict[str, str] = {}
    by_pod: Dict[str, int] = {}
    for fp, raw in carried.items():
        if fp in already:
            continue
        pod = json.loads(raw).get("pod_id", "?")
        if pod_filter is not None and pod not in pod_filter:
            continue
        missing[fp] = raw
        by_pod[pod] = by_pod.get(pod, 0) + 1

    logger.info(
        "recover: scanned %d archive(s) + active log; %d open position(s) missing from the active log %s",
        len(archives), len(missing), by_pod or "",
    )
    if dry_run or not missing:
        return {"recovered": 0, "missing": len(missing), "by_pod": by_pod, "archives": [str(p) for p in archives]}

    with open(log_path, "a", encoding="utf-8") as fh:
        for raw in missing.values():
            fh.write(raw + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    return {"recovered": len(missing), "missing": len(missing), "by_pod": by_pod,
            "archives": [str(p) for p in archives]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    ap.add_argument("--cap-bytes", type=int, default=DEFAULT_CAP_BYTES)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--recover-open-from-archives", type=int, default=0, metavar="N",
        help="Instead of rotating, scan the N most recent archives and re-append "
             "any unresolved PLACED rows missing from the active log.",
    )
    ap.add_argument(
        "--pod", action="append", default=None, metavar="POD_ID",
        help="Restrict --recover-open-from-archives to these pods (repeatable). "
             "Strongly recommended: recovering a pod with no settler pins "
             "exposure in the risk guard that will never drain.",
    )
    args = ap.parse_args(argv)

    if args.recover_open_from_archives:
        out = recover_open_from_archives(
            args.log_path, args.recover_open_from_archives,
            dry_run=args.dry_run, pods=args.pod,
        )
    else:
        out = rotate(args.log_path, args.cap_bytes, dry_run=args.dry_run)

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
