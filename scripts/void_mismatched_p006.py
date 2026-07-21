#!/usr/bin/env python3
"""
void_mismatched_p006.py
───────────────────────
One-shot script to void open P-006 trades where the Polymarket question
does not contain both teams from the Odds API event.

Run on the VPS:
    cd /opt/betting-pod-shop
    python3 scripts/void_mismatched_p006.py

Appends VOID records (with reason="TEAM_MISMATCH_RETROACTIVE") to the
trade log for each mismatched open trade.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.fuzzy_utils import both_teams_present
from src.polymarket_matcher import _extract_teams_from_question

LOG_PATH = ROOT / "data" / "trade_logs" / "trade_log.jsonl"


def main():
    # Load all records
    all_records = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            all_records.append(rec)

    # Group P-006 by fingerprint
    p006_by_fp = defaultdict(list)
    for rec in all_records:
        if (rec.get("pod_id") == "P-006"
                and rec.get("action") not in ("SKIPPED_EDGE", "SKIPPED_RISK")):
            fp = rec.get("fingerprint", "")
            if fp:
                p006_by_fp[fp].append(rec)

    # Find open trades with team mismatches
    void_records = []
    now = datetime.now(timezone.utc).isoformat()

    for fp, records in p006_by_fp.items():
        placed = [r for r in records if r.get("action") == "PLACED"]
        settled = [r for r in records if r.get("action") in ("WIN", "LOSS", "VOID")]
        if not placed or settled:
            continue  # already settled or voided

        p = placed[0]
        extra = p.get("extra", {})
        if not isinstance(extra, dict):
            continue
        question = extra.get("polymarket_question", "")
        event = p.get("event", "")

        parts = event.split(" vs ")
        if len(parts) != 2:
            continue
        home, away = parts[0].strip(), parts[1].strip()

        match_text = _extract_teams_from_question(question)
        if both_teams_present(home, away, match_text):
            continue  # correctly matched — leave open

        # Mismatch → void
        void_rec = {
            "timestamp_utc": now,
            "pod_id": "P-006",
            "pod_name": p.get("pod_name", ""),
            "action": "VOID",
            "reason": "TEAM_MISMATCH_RETROACTIVE",
            "fingerprint": fp,
            "event": event,
            "polymarket_question": question,
            "position_size_usd": p.get("position_size_usd", 0),
            "pnl_usd": 0,
        }
        void_records.append(void_rec)

    if not void_records:
        print("No mismatched open trades found. Nothing to void.")
        return

    # Append void records to trade log
    with open(LOG_PATH, "a") as f:
        for rec in void_records:
            f.write(json.dumps(rec) + "\n")

    print(f"Voided {len(void_records)} mismatched P-006 trades.")
    print("Breakdown:")
    sports = defaultdict(int)
    for rec in void_records:
        sport = rec.get("event", "unknown")
        sports[sport] += 1
    for event, count in sorted(sports.items()):
        print(f"  {event}")


if __name__ == "__main__":
    main()
