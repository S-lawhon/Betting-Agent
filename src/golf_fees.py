"""
src/golf_fees.py  — COMPATIBILITY SHIM
──────────────────────────────────────
The golf fee/de-vig helpers have been promoted into the canonical modules:
  • series-aware fees  → src/kalshi_fees.py (series_maker_charges_fee,
    fee_per_contract(..., series_ticker=...))
  • power/Shin de-vig  → src/devig.py (devig_power)

This module re-exports them so any code that imports from src.golf_fees
keeps working. Prefer the canonical locations in new code. (The standalone
backtest under golf_research/backtest/ keeps its own local copy and does
not import this shim.)
"""
from src.kalshi_fees import (  # noqa: F401
    fee_per_contract,
    fee_total,
    series_maker_charges_fee,
)
from src.devig import (  # noqa: F401
    devig_power,
    devig_multiplicative,
)
