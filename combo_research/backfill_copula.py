#!/usr/bin/env python3
"""
combo_research/backfill_copula.py
─────────────────────────────────
Re-price shadow-DB rows whose ``copula_price`` is NULL, offline, from the raw
leg marks stored at discovery.

Why this exists
───────────────
Rows logged before the copula landed carry no correlation-adjusted price — and
on the droplet the ``combo`` table predates the columns entirely. But every row
stores ``leg_marks_json``, the RAW leg books at the moment of discovery,
precisely so this backfill can run without re-collecting anything. Re-pricing
from stored marks is *exactly* equivalent to what the live path would have
computed: both use discovery-time mids.

Rules honoured here:

* **Never restart the shadow logger for this.** It is single-instance locked
  and collecting; a gap in the tape cannot be backfilled. This script operates
  on the DB alongside it (WAL mode is on) with short write transactions.
* **Idempotent.** Rows that already carry a ``copula_price`` are untouched
  unless ``--force`` is passed.
* **Schema migration is additive and idempotent** — ``ALTER TABLE ADD COLUMN``
  only when the column is missing. It never rewrites existing data.
* ``model_price`` and ``in_zone`` are deliberately NOT rewritten: they were
  computed at discovery and re-deriving them retroactively would silently
  re-define the Gate 0 sample. The gate report reads ``copula_price`` directly.

Usage::

    python3 backfill_copula.py --db /var/lib/p029/shadow.sqlite \
        --rho combo_research/fitted_rho.json
    python3 backfill_copula.py --db ... --rho ... --force     # re-price all
    python3 backfill_copula.py --db ... --rho ... --dry-run   # count only
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.combo_copula import (  # noqa: E402
    ComboCopula, correlation_matrix, load_rho, relation,
)

log = logging.getLogger("backfill")

BATCH = 500          # rows per write transaction — keep locks short (WAL)


def ensure_columns(con: sqlite3.Connection) -> List[str]:
    """Add copula_price / leg_relation if the table predates them."""
    have = {r[1] for r in con.execute("PRAGMA table_info(combo)")}
    added = []
    for col, typ in (("copula_price", "REAL"), ("leg_relation", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE combo ADD COLUMN {col} {typ}")
            added.append(col)
    con.commit()
    return added


def strongest_relation(tickers: List[str]) -> str:
    order = {"same_player": 3, "same_game": 2, "same_day": 1, "cross": 0}
    best = "cross"
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            r = relation(tickers[i], tickers[j])
            if order[r] > order[best]:
                best = r
    return best


def effective_marginals(legs: List[Tuple[str, str]],
                        marks: List[dict]) -> Optional[List[float]]:
    """Per-leg P(pays) from the stored discovery-time marks.

    Marks are looked up by ticker, not position — the stored list is in leg
    order today, but nothing in the schema guarantees it.
    """
    by_ticker: Dict[str, dict] = {}
    for m in marks or []:
        t = m.get("ticker")
        if t and t not in by_ticker:
            by_ticker[t] = m
    eff: List[float] = []
    for tkr, side in legs:
        rec = by_ticker.get(tkr)
        mid = (rec or {}).get("yes_mid")
        if mid is None:
            return None
        p = float(mid)
        if (side or "yes").lower() == "no":
            p = 1.0 - p
        if not (0.0 < p < 1.0):
            return None
        eff.append(p)
    return eff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--rho", default=str(Path(__file__).parent / "fitted_rho.json"),
                    help="fitted-rho JSON from fit_correlation.py; unfitted "
                         "blocks fall back to the priors")
    ap.add_argument("--force", action="store_true",
                    help="re-price rows that already have a copula_price")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    rho = load_rho(a.rho)
    log.info("rho in use: %s", rho)
    cop = ComboCopula(rho=rho)

    con = sqlite3.connect(a.db, timeout=60.0)
    con.execute("PRAGMA busy_timeout=60000")
    added = ensure_columns(con)
    if added:
        log.info("added columns: %s", ", ".join(added))

    where = "" if a.force else "WHERE copula_price IS NULL"
    total = con.execute(f"SELECT COUNT(*) FROM combo {where}").fetchone()[0]
    log.info("rows to price: %d%s", total, " (force)" if a.force else "")
    if a.dry_run:
        con.close()
        return 0

    rows = con.execute(
        f"SELECT market_ticker, legs_json, leg_marks_json FROM combo {where}"
    ).fetchall()

    done = skipped = 0
    pending: List[Tuple[float, str, str]] = []
    for tkr, legs_j, marks_j in rows:
        try:
            legs = [(l[0], l[1]) for l in json.loads(legs_j or "[]")]
            marks = json.loads(marks_j or "[]")
        except (ValueError, IndexError, TypeError):
            skipped += 1
            continue
        eff = effective_marginals(legs, marks)
        if not eff or len(eff) != len(legs):
            skipped += 1
            continue
        tickers = [t for t, _ in legs]
        try:
            corr = correlation_matrix(tickers, rho)
            price = cop.joint_yes(eff, corr)
        except Exception as exc:
            log.warning("pricing failed on %s: %s", tkr, exc)
            skipped += 1
            continue
        pending.append((price, strongest_relation(tickers), tkr))
        if len(pending) >= BATCH:
            con.executemany(
                "UPDATE combo SET copula_price=?, leg_relation=? "
                "WHERE market_ticker=?", pending)
            con.commit()
            done += len(pending)
            pending = []
            if done % 10_000 < BATCH:
                log.info("priced %d/%d", done, total)
    if pending:
        con.executemany(
            "UPDATE combo SET copula_price=?, leg_relation=? "
            "WHERE market_ticker=?", pending)
        con.commit()
        done += len(pending)

    log.info("done: priced %d, skipped %d (unusable marks), of %d",
             done, skipped, total)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
