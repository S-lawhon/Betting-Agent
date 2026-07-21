"""Kalshi fee model + net-edge gate.

Kalshi taker fee = round_up_to_cent(0.07 * C * P * (1-P)); maker = 0.0175 * ...
The P*(1-P) term peaks at P=0.5, so fees bite hardest on ~50c game contracts —
exactly where competitive moneylines cluster. Net edge MUST clear fee + spread.

SERIES-AWARE MAKER FEES (added 2026-07 for P-017 golf):
Not all series charge maker fees. Kalshi's `quadratic` fee_type charges
makers ZERO; `quadratic_with_maker_fees` charges makers the 0.0175 rate.
Golf derivative series (top-N, make-cut, H2H, 3-ball, round leaders) are
`quadratic` → zero maker fee; golf winner/outright series are
`quadratic_with_maker_fees`. Pass `series_ticker` to fee_per_contract /
fee_total to get the correct maker treatment. Backward compatible: with no
series_ticker, maker fees fall back to the general 0.0175 rate (so existing
callers, e.g. P-016, are unchanged).
"""
from __future__ import annotations
import math

TAKER_COEF = 0.07
MAKER_COEF = 0.0175

# Series that charge maker fees (fee_type == "quadratic_with_maker_fees").
# Verified against live /series metadata (2026-07-19). Everything NOT matching
# these prefixes is treated as a zero-maker-fee (`quadratic`) series.
_MAKER_FEE_SERIES_PREFIXES = (
    "KXPGATOUR", "KXTHEOPEN", "KXPGARYDER", "KXPGASOLHEIM",
    "KXLPGATOUR", "KXLIVTOUR", "KXCHAMPTOUR",
)
# Exact matches that need special handling (KXPGA is the PGA Championship
# winner series; guard against matching KXPGATOP*, KXPGAMAKECUT, etc.).
_MAKER_FEE_SERIES_EXACT = ("KXPGA",)


def series_maker_charges_fee(series_ticker: str) -> bool:
    """True if this series charges maker fees (quadratic_with_maker_fees).

    Golf prop series (top-N, make-cut, H2H, 3-ball, round leaders) return
    False → maker fee is zero. Winner/outright series return True. Unknown
    non-golf series also return True (conservative: assume maker fees).
    """
    s = (series_ticker or "").upper()
    if not s:
        return True  # unknown → conservative (charge)
    if s in _MAKER_FEE_SERIES_EXACT:
        return True
    if any(s.startswith(p) for p in _MAKER_FEE_SERIES_PREFIXES):
        return True
    # Known zero-maker-fee golf prop families
    _ZERO_MAKER_GOLF = (
        "KXPGATOP", "KXPGAMAKECUT", "KXPGAH2H", "KXGOLFH2H", "KXPGA3BALL",
        "KXPGA5BALL", "KXPGAR1LEAD", "KXPGAR2LEAD", "KXPGAR3LEAD",
        "KXPGAUNDERPAR", "KXDPWTH2H", "KXLIVH2H", "KXPGACUTLINE",
    )
    if any(s.startswith(p) for p in _ZERO_MAKER_GOLF):
        return False
    # Any other golf-ish prop or unknown series: default to charging (safe).
    return True


def _roundup_cent(x: float) -> float:
    return math.ceil(x * 100.0 - 1e-9) / 100.0


def fee_per_contract(price: float, maker: bool = False,
                     series_ticker: str = "") -> float:
    """Marginal (un-rounded) fee for ONE contract at `price`, in dollars.

    Taker: 0.07*P*(1-P). Maker: 0 for zero-maker-fee series (golf props when
    series_ticker is given), else 0.0175*P*(1-P). With no series_ticker the
    maker path uses the general 0.0175 rate (backward compatible).
    """
    if not maker:
        return TAKER_COEF * price * (1.0 - price)
    if series_ticker and not series_maker_charges_fee(series_ticker):
        return 0.0
    return MAKER_COEF * price * (1.0 - price)


def fee_total(contracts: float, price: float, maker: bool = False,
              series_ticker: str = "") -> float:
    """Actual fee charged on an order (rounded up to the cent), in dollars."""
    per = fee_per_contract(price, maker, series_ticker)
    return _roundup_cent(per * contracts)


def fee_fraction_of_stake(price: float, maker: bool = False,
                          series_ticker: str = "") -> float:
    """Fee as a fraction of dollars staked. Highest for cheap (dog) contracts,
    lowest for favourites — one reason the MLB edge concentrates in favourites.
    """
    if price <= 0:
        return 0.0
    if maker and series_ticker and not series_maker_charges_fee(series_ticker):
        return 0.0
    coef = MAKER_COEF if maker else TAKER_COEF
    return coef * (1.0 - price)


def net_edge(fair_prob: float, price: float, maker: bool = False,
             half_spread: float = 0.0, series_ticker: str = "") -> float:
    """Per-contract net edge in dollars: worth `fair_prob`, pay `price`, minus
    the fee and half the bid-ask. Bet only when > 0."""
    return (fair_prob - price) - fee_per_contract(price, maker, series_ticker) \
        - half_spread


def should_bet(fair_prob: float, price: float, maker: bool = False,
               half_spread: float = 0.0, min_net_edge: float = 0.0,
               series_ticker: str = "") -> bool:
    return net_edge(fair_prob, price, maker, half_spread, series_ticker) \
        > min_net_edge
