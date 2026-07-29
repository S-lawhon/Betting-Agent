#!/usr/bin/env python3
"""A/B the P-022 size screen: same injected clock, screen on (config) vs off.

Uses the pod's own from_config -> discover -> cycle path, like
scripts/p022_dry_run.py, and reports how many quotes survive the screen.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[1])          # repo root
from src.config_loader import load_config
from src.round_leader_fade_maker import RoundLeaderFadeMakerPod


def _epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def run(at_iso, min_top_size):
    now = _epoch(at_iso)
    config = load_config()
    q = (config.setdefault("pods", {}).setdefault("P-022", {})
               .setdefault("quoting", {}))
    q["min_top_size"] = min_top_size
    tmp = Path(tempfile.mkdtemp(prefix="p022ab_"))
    eng = RoundLeaderFadeMakerPod.from_config(
        config, log_dir=tmp, _now_fn=lambda: now,
        kill_file=tmp / "KILL_NEVER")
    n = eng.discover()
    # bound work to in-window books, as the dry-run harness does
    keep = [tk for tk, b in eng.books.items()
            if eng.no_new_quote_h <= (b.close_epoch - now) / 3600.0
            <= eng.fade_start_h]
    for tk in list(eng.books):
        if tk not in keep:
            del eng.books[tk]
    eng.cycle()
    quotes = [(tk, b.ask_quote.price, b.ask_quote.qty)
              for tk, b in sorted(eng.books.items()) if b.ask_quote]
    refused = []
    log = tmp / "round_leader_fade_quotes.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("type") == "REFUSED":
                refused.append({k: rec.get(k) for k in
                                ("ticker", "book_side", "yes_bid", "yes_ask",
                                 "bid_qty", "ask_qty")})
    return {"discovered": n, "in_window": len(keep),
            "quotes": quotes, "refused": refused}


if __name__ == "__main__":
    for at in sys.argv[2:]:
        off = run(at, 0)
        on = run(at, 100)
        print(f"\n=== {at} ===")
        print(f"discovered {on['discovered']}  in_window {on['in_window']}")
        print(f"screen OFF: {len(off['quotes'])} quotes")
        for t, p, s in off["quotes"]:
            print(f"    {t}  ask {p:.2f} x {s:.0f}")
        print(f"screen ON (min_top_size=100): {len(on['quotes'])} quotes, "
              f"{len(on['refused'])} refused")
        for t, p, s in on["quotes"]:
            print(f"    {t}  ask {p:.2f} x {s:.0f}")
        for r in on["refused"]:
            print(f"    REFUSED {r['ticker']}  side={r['book_side']} "
                  f"bid={r['yes_bid']} ask={r['yes_ask']} "
                  f"bid_qty={r['bid_qty']} ask_qty={r['ask_qty']}")
