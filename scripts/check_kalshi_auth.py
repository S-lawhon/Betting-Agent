#!/usr/bin/env python3
"""
scripts/check_kalshi_auth.py
────────────────────────────
Verify Kalshi API credentials. **Read-only — this script cannot place an order.**

Run it right after rotating a key, and again after creating the read-only
research key, to confirm which scopes each one actually has.

Usage::

    python3 scripts/check_kalshi_auth.py
    python3 scripts/check_kalshi_auth.py --probe-write   # detect scope only

``--probe-write`` sends a deliberately INVALID order (count=0) to see whether
the key is even permitted to reach the endpoint. A validation error means the
key has write scope; a 401/403 means it does not. Either way **no order can be
created**, because the exchange rejects it before matching.

Prints no key material.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env_loader import load
from src.kalshi_private import (KalshiAuthError, KalshiPrivate, OrdersDisabled,
                                settlement_pnl)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-write", action="store_true",
                    help="detect whether the key has write scope (creates nothing)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    load()

    try:
        k = KalshiPrivate.from_env()
    except KalshiAuthError as exc:
        print(f"✗ credentials not usable: {exc}")
        print("\n  Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env.")
        print("  Prefer the PATH form — an inline PEM can be echoed by a")
        print("  careless read of .env, which is how a key leaked on 2026-07-28.")
        return 2

    print(f"key id     : {k.auth.key_id[:8]}…")

    bal = k.get_balance()
    if bal is None:
        print("✗ authentication FAILED — /portfolio/balance did not return.")
        print("  Most likely: the key was deleted, or the key ID and private")
        print("  key are from different pairs. Check Kalshi → Settings → API Keys.")
        return 1
    print(f"✓ authenticated")
    print(f"balance    : ${bal:,.2f}")

    pos = k.get_positions()
    fills = k.get_fills(limit=100)
    print(f"positions  : {len(pos)}")
    print(f"recent fills: {len(fills)}")

    # Kalshi returns NO P&L field on settlements — it must be derived from
    # revenue (cents) minus the cost/fee fields (dollars). Summing a field that
    # does not exist is what silently reported $0.00 here and blinded the loss
    # guard on a live account.
    setts = k.get_settlements(limit=100)
    vals = [settlement_pnl(r) for r in setts]
    readable = [v for v in vals if v is not None]
    realised = sum(readable)
    print(f"settlements: {len(setts)}  "
          f"(derived P&L on this page: ${realised:+,.2f} "
          f"from {len(readable)}/{len(setts)} readable rows)")
    if setts and not readable:
        print("  ✗ NO settlement row yielded a P&L value — the loss guard would")
        print("    be blind. Run scripts/inspect_settlements.py before trading.")

    if a.probe_write:
        print("\nprobing write scope (creates nothing — count=0 is always rejected)")
        probe = KalshiPrivate(k.auth, allow_orders=True)
        resp = probe.request("POST", "/portfolio/orders",
                             body={"ticker": "SCOPE-PROBE", "action": "buy",
                                   "side": "yes", "count": 0, "type": "limit",
                                   "yes_price": 1})
        codes = [c for _, p, c in probe.status_log if p == "/portfolio/orders"]
        if not codes and resp is None:
            print("  ? inconclusive — no response")
        elif any(c in (401, 403) for c in codes):
            print("  → READ-ONLY key (write rejected). Correct for Phases 0–1.")
        else:
            print(f"  → key HAS WRITE SCOPE (HTTP {codes or 'accepted'}).")
            print("    Use a read-only key for research phases.")

    print("\nOrders are disabled on this client by construction:")
    try:
        k.place_order(ticker="X", side="yes", count=1, yes_price=5)
        print("  ✗ UNEXPECTED: an order was not refused — do not trade with this build")
        return 1
    except OrdersDisabled:
        print("  ✓ refused, as designed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
