#!/usr/bin/env python3
"""
scripts/setup_subaccount.py
───────────────────────────
Set up and VERIFY the dedicated subaccount that isolates P-029 from manual
trading.

Why a subaccount and not a second account: Kalshi permits **one account per
person** — *"users are restricted to trading with only a single account"* — and
rejects further registrations automatically. Subaccounts (0–63) are the
sanctioned alternative, and because they are **API-only** (not exposed in the
web or mobile app) manual GUI trading physically cannot reach one.

What this buys us:
  * the $500 loss guard counts only the pod's own losses, so a bad manual day
    cannot halt the experiment, and the pod's losses cannot hide inside manual
    P&L;
  * Phase 3's realised-edge measurement reads fills and settlements that belong
    to the pod alone.

Default is **inspect-only**. Nothing moves without ``--transfer``.

Usage::

    python3 scripts/setup_subaccount.py                       # inspect
    python3 scripts/setup_subaccount.py --transfer 600        # 0 -> 1
    python3 scripts/setup_subaccount.py --verify              # isolation proof
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env_loader import load
from src.kalshi_private import KalshiPrivate, settlement_pnl

TARGET = 1          # the P-029 subaccount


def _summarise(k: KalshiPrivate, n: int) -> dict:
    scoped = KalshiPrivate(k.auth, subaccount=n)
    bal = scoped.get_balance()
    pos = scoped.get_positions()
    fills = scoped.get_fills(limit=200)
    setts = scoped.get_settlements(limit=200)
    pnl = sum(v for v in (settlement_pnl(r) for r in setts) if v is not None)
    return {"subaccount": n, "balance": bal, "positions": len(pos),
            "fills": len(fills), "settlements": len(setts), "pnl": pnl}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transfer", type=float, metavar="DOLLARS",
                    help=f"move cash from subaccount 0 into {TARGET}")
    ap.add_argument("--from-subaccount", type=int, default=0)
    ap.add_argument("--to-subaccount", type=int, default=TARGET)
    ap.add_argument("--verify", action="store_true",
                    help="check the two subaccounts really are separate")
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load()

    reader = KalshiPrivate.from_env()
    print(f"key id: {reader.auth.key_id[:8]}…\n")

    print(f"{'subaccount':>11}  {'balance':>11}  {'positions':>9}  "
          f"{'fills':>6}  {'settled':>7}  {'realised P&L':>13}")
    rows = {}
    for n in sorted({0, a.to_subaccount, TARGET}):
        try:
            s = _summarise(reader, n)
        except Exception as exc:
            print(f"{n:>11}  <error: {exc}>")
            continue
        rows[n] = s
        bal = "n/a" if s["balance"] is None else f"${s['balance']:,.2f}"
        print(f"{n:>11}  {bal:>11}  {s['positions']:>9}  {s['fills']:>6}  "
              f"{s['settlements']:>7}  {s['pnl']:>+13,.2f}")

    if a.transfer:
        src, dst = a.from_subaccount, a.to_subaccount
        print(f"\nmoving ${a.transfer:,.2f} from subaccount {src} -> {dst}")
        mover = KalshiPrivate.from_env(allow_orders=True)
        out = mover.transfer_subaccount(from_subaccount=src, to_subaccount=dst,
                                        amount_dollars=a.transfer)
        if out is None:
            print("  ✗ transfer failed — see the warning above")
            print("    If subaccounts are not enabled on this account, contact")
            print("    Kalshi support; the feature is API-only and may need")
            print("    activation.")
            return 1
        print("  ✓ transfer accepted")
        for n in (src, dst):
            s = _summarise(reader, n)
            bal = "n/a" if s["balance"] is None else f"${s['balance']:,.2f}"
            print(f"    subaccount {n} balance now {bal}")

    if a.verify:
        print("\n=== ISOLATION CHECK ===")
        primary = rows.get(0)
        target = rows.get(a.to_subaccount)
        if not primary or not target:
            print("  could not read both subaccounts — cannot verify")
            return 1
        ok = True
        if a.to_subaccount == 0:
            print("  ✗ target IS the primary subaccount — no isolation at all")
            ok = False
        if target["settlements"] and target["settlements"] == primary["settlements"]:
            print("  ✗ both report the same settlement count — the subaccount")
            print("    filter may be ignored. Do NOT rely on it for the loss guard.")
            ok = False
        if target["fills"] and target["fills"] == primary["fills"]:
            print("  ✗ both report the same fill count — filter likely ignored")
            ok = False
        if ok and target["settlements"] == 0 and target["fills"] == 0:
            print(f"  ✓ subaccount {a.to_subaccount} is clean: no prior fills or")
            print("    settlements, so the loss budget starts from zero")
        if ok and primary["settlements"] > target["settlements"]:
            print(f"  ✓ primary has {primary['settlements']} settlements vs "
                  f"{target['settlements']} — the filter is doing something")
        print("\n  Set the pod's client to subaccount "
              f"{a.to_subaccount}, and give LossGuard a `since` at the start of")
        print("  the experiment. Both, not either.")
        return 0 if ok else 1

    if not a.transfer:
        print("\n(inspect-only — pass --transfer DOLLARS to move cash, "
              "--verify to check isolation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
