"""
src/trade_log_schema.py
───────────────────────
Canonical schema definition for trade log JSONL entries.

Provides:
  - ``CANONICAL_FIELDS``: ordered list of fields every new entry should have.
  - ``TradeLogSchema.normalize(entry)``: normalize + validate a single entry.
  - ``TradeLogSchema.validate(entry)``: returns list of issues.
  - ``TradeLogSchema.migrate_file(path)``: rewrite a trade log with all entries
    normalized (for one-time migration of the 62K+ entry log).
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Canonical field names ────────────────────────────────────────────
# Every PLACED entry should have these fields (some optional for settlement
# entries).  Order reflects logical grouping.

CANONICAL_FIELDS = [
    # Identity
    "fingerprint",
    "timestamp_utc",
    "pod_id",
    "pod_name",
    "action",              # PLACED, WIN, LOSS, VOID
    # Market
    "venue",               # kalshi, polymarket, forecastex, multi
    "market_type",         # sports_baseball, economics_cpi, cross_venue, etc.
    "event",               # "Team A vs. Team B" or economic event description
    "market_id",           # canonical market identifier
    "side",                # YES, NO
    # Pricing
    "fair_prob",
    "venue_prob",
    "edge_pct",
    "ev",
    "kelly_fraction",
    # Position
    "position_size_usd",
    "fill_price",
    "contracts",
    "mode",                # paper, live
    # Settlement (optional)
    "outcome",             # WIN, LOSS, VOID
    "pnl_usd",
    "settled_at_utc",
    # Metadata
    "extra",               # dict of pod-specific metadata
]

# Fields that map from legacy names → canonical names
_FIELD_ALIASES = {
    "market_ticker": "market_id",
    "kalshi_prob":   "venue_prob",
}

# Default values for missing fields in legacy entries
_DEFAULTS = {
    "pod_id":   "P-001",
    "pod_name": "Kalshi vs Sharp's Strategy",
    "venue":    "kalshi",
    "mode":     "paper",
}


class TradeLogSchema:
    """Centralized schema normalization and validation for trade log entries."""

    @staticmethod
    def normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
        """Return a normalized copy of *entry* with canonical field names.

        - Renames legacy fields (``market_ticker`` → ``market_id``, etc.)
        - Applies defaults for missing identity fields
        - Coerces numeric types
        - Preserves unknown fields in ``extra``
        """
        out = dict(entry)

        # Alias mapping (keep originals for backward compat, add canonical)
        for old_key, new_key in _FIELD_ALIASES.items():
            if old_key in out and new_key not in out:
                out[new_key] = out[old_key]

        # Apply defaults
        for field, default in _DEFAULTS.items():
            if field not in out:
                out[field] = default

        # Coerce numeric fields
        for numeric_field in ("fair_prob", "venue_prob", "edge_pct", "ev",
                               "kelly_fraction", "position_size_usd",
                               "fill_price", "pnl_usd"):
            if numeric_field in out and out[numeric_field] is not None:
                try:
                    out[numeric_field] = float(out[numeric_field])
                except (ValueError, TypeError):
                    pass

        # Normalize pnl: prefer pnl_usd, fall back to pnl
        if "pnl_usd" not in out and "pnl" in out:
            out["pnl_usd"] = out["pnl"]

        # Normalize outcome from action field for settlement entries
        action = (out.get("action") or "").upper()
        if action in ("WIN", "LOSS", "VOID") and "outcome" not in out:
            out["outcome"] = action

        return out

    @staticmethod
    def validate(entry: Dict[str, Any]) -> List[str]:
        """Return a list of schema issues for the given entry.

        An empty list means the entry is fully valid.
        """
        issues = []

        # Required fields for PLACED entries
        action = (entry.get("action") or "").upper()
        if action == "PLACED":
            for field in ("fingerprint", "timestamp_utc", "pod_id", "venue",
                          "market_id", "side"):
                if not entry.get(field):
                    issues.append(f"missing required field: {field}")

            # ── fill_price is REQUIRED on PLACED ──────────────────────
            # This check used to be "numeric IF not None", which is not a
            # required-field check at all — a null passed it silently.
            # P-014 wrote `fill_price: null` on 356 of 356 PLACED rows
            # over four months and validation never objected.  A PLACED
            # row with no price cannot be billed (fee = 0.07*P*(1-P)) and
            # cannot be converted to contracts (size / price), so treat
            # its absence as a hard schema issue, not a missing extra.
            fill = entry.get("fill_price")
            if fill is None:
                issues.append("missing required field: fill_price")
            else:
                try:
                    fill_val = float(fill)
                except (ValueError, TypeError):
                    issues.append(f"non-numeric fill_price: {fill!r}")
                else:
                    # Every consumer divides by this (contracts = size /
                    # fill_price), so zero is as unusable as null.
                    if fill_val <= 0:
                        issues.append(f"non-positive fill_price: {fill!r}")

            # Numeric checks
            for field in ("position_size_usd",):
                val = entry.get(field)
                if val is not None:
                    try:
                        float(val)
                    except (ValueError, TypeError):
                        issues.append(f"non-numeric {field}: {val!r}")

        # Settlement entries
        elif action in ("WIN", "LOSS", "VOID"):
            if not entry.get("fingerprint") and not entry.get("market_id"):
                issues.append("settlement entry needs fingerprint or market_id")

        return issues

    @staticmethod
    def migrate_file(
        path: Path,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Rewrite a trade log file with all entries normalized.

        Returns stats dict: {total, normalized, issues, skipped_lines}
        """
        if not path.exists():
            return {"total": 0, "normalized": 0, "issues": 0, "skipped_lines": 0}

        entries = []
        skipped = 0
        total_issues = 0

        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue

                normalized = TradeLogSchema.normalize(entry)
                issues = TradeLogSchema.validate(normalized)
                if issues:
                    total_issues += len(issues)
                entries.append(normalized)

        logger.info(
            "migrate_file: %d entries, %d skipped, %d validation issues",
            len(entries), skipped, total_issues,
        )

        if dry_run:
            return {
                "total": len(entries),
                "normalized": len(entries),
                "issues": total_issues,
                "skipped_lines": skipped,
            }

        # Atomic rewrite
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="migration_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                for entry in entries:
                    f.write(json.dumps(entry, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return {
            "total": len(entries),
            "normalized": len(entries),
            "issues": total_issues,
            "skipped_lines": skipped,
        }
