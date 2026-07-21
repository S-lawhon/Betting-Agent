"""Closing Line Value harness. CLV vs the de-vigged sharp CLOSE is the fastest,
lowest-variance proxy for genuine edge (significance in ~50 bets for a large
edge vs thousands for P&L). This is the north-star metric for the Kalshi rebuild.
"""
from __future__ import annotations
from typing import Optional, Dict, Any
from src.kalshi_fees import fee_per_contract

# New trade-log fields the engine should populate per bet (see log schema).
CLV_FIELDS = (
    "sharp_fair_entry",   # de-vigged sharp fair prob of bet side, at execution
    "sharp_fair_close",   # de-vigged sharp fair prob of bet side, at close
    "entry_price",        # Kalshi price paid for the bet side
    "half_spread",        # half the Kalshi bid-ask at entry (price units)
    "maker",              # bool: was this a maker (limit) fill
    "clv_gross",          # sharp_fair_close - entry_price
    "clv_net",            # clv_gross - fee_per_contract - half_spread
)


def clv_gross(entry_price: float, sharp_fair_close: float) -> float:
    """Gross CLV in probability/price units: how much the bet side was worth at
    the sharp close vs what we paid. Positive = we beat the closing line."""
    return sharp_fair_close - entry_price


def clv_net(entry_price: float, sharp_fair_close: float, maker: bool = False,
            half_spread: float = 0.0) -> float:
    """CLV net of Kalshi fee and half-spread — the honest edge estimate."""
    return clv_gross(entry_price, sharp_fair_close) \
        - fee_per_contract(entry_price, maker) - half_spread


def clv_record(entry_price: float, sharp_fair_entry: float,
               sharp_fair_close: Optional[float], maker: bool = False,
               half_spread: float = 0.0) -> Dict[str, Any]:
    """Build the CLV fields dict to merge into a settled trade-log record.
    sharp_fair_close may be None until the market closes (fill later)."""
    rec: Dict[str, Any] = {
        "sharp_fair_entry": sharp_fair_entry,
        "sharp_fair_close": sharp_fair_close,
        "entry_price": entry_price,
        "half_spread": half_spread,
        "maker": maker,
    }
    if sharp_fair_close is not None:
        rec["clv_gross"] = clv_gross(entry_price, sharp_fair_close)
        rec["clv_net"] = clv_net(entry_price, sharp_fair_close, maker, half_spread)
    return rec
