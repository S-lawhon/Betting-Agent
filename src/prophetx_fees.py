"""ProphetX fee model + the Kalshi<->ProphetX cross-venue corridor.

WHY THIS IS A SEPARATE MODULE FROM kalshi_fees.py
--------------------------------------------------
ProphetX's fee is a **different mathematical object** and must not be modelled
with kalshi_fees.py's shape. From the ProphetX Trading Fee Schedule (v1.0,
effective 2026-06-10):

    2% trading fee on NET GAINS PER MARKET; 0% on parlays.

Three consequences that break the Kalshi analogy:

  1. It is charged on the WINNING leg only. A losing position costs nothing.
     So it is *stochastic*, not a per-contract cost. Code that treats it as a
     deterministic per-contract debit will overstate cost on losers and
     understate the variance of a "locked" arb.
  2. It is levied on the GAIN (1 - entry), not on stake and not on notional.
  3. Netting is PER MARKET. Multiple positions in the same market net gains
     against losses before the 2% applies. Cross-venue legs never net --
     different exchange, different clearing.

Expected cost is what makes it comparable to Kalshi. A leg bought at price q
wins with probability q and gains (1-q), so:

    E[fee] = q * 0.02 * (1-q) = 0.02 * q(1-q)

which is the SAME parabola as Kalshi's, with coefficient 2 instead of 7.
Hence the routing rule that falls out of this file:

    ProphetX taker is exactly 3.5x cheaper than Kalshi taker at EVERY price.
    -> TAKE on ProphetX. MAKE on Kalshi's 12,169 maker-free series.

CAVEAT -- MAKE VOLUME FEE REBATE PROGRAM. ProphetX self-certified a marginal
maker rebate (up to 80% above $7.5M monthly make volume, open to all
participants on objective thresholds). Its stated initial term is TWO MONTHS
from 2026-06-29, i.e. nominally expiring ~2026-08-29, extension discretionary
and requiring a new CFTC filing. `REBATE_TIERS` is recorded for reference and
is DELIBERATELY NOT applied by default. Do not underwrite on it.

SOURCES: prophetx.co/lobby/t-c/trading-fees; CFTC self-certification filings at
prophetx.co/lobby/t-c/filings; Rulebook v2.0 eff. 2026-07-27 (Rule 4.6).
"""
from __future__ import annotations

from typing import Optional

PX_FEE_RATE = 0.02          # 2% of net gains per market
PX_PARLAY_FEE_RATE = 0.0    # parlays are free

KALSHI_TAKER_COEF = 0.07
KALSHI_MAKER_COEF = 0.0175

# Rule 4.6(d): "A range equal to fifteen percent (15%) above and below Fair
# Market Value (the 'No-Cancellation Range') will generally be applied ...
# Trades outside the No-Cancellation Range may be cancelled or adjusted at
# ProphetX DCM's sole discretion."
#
# MULTIPLICATIVE -- 15% OF THE PRICE, not 15 percentage points. This is the
# single most misread number in the venue's rulebook and it inverts where the
# strategy should trade: the band is +/-7.5c at a 50c contract but only
# +/-1.5c at a 10c contract. The fee parabola points at the tails; this points
# away from them, and it wins. See corridor_table().
NO_CANCEL_FRAC = 0.15

# 2026 amendment to IRC s165(d): only 90% of wagering losses are deductible.
DEDUCTIBLE_LOSS_FRAC = 0.90

# Marginal rebate on FEES PAID, by monthly make volume. NOT applied by default.
REBATE_TIERS = ((50_000, 0.00), (750_000, 0.20), (3_000_000, 0.40),
                (7_500_000, 0.60), (float("inf"), 0.80))


# ── ProphetX leg ─────────────────────────────────────────────────────────────

def realized_fee_per_contract(entry_price: float, won: bool) -> float:
    """Fee actually charged on ONE contract, in dollars. Zero on a loser."""
    if not won:
        return 0.0
    if not 0.0 <= entry_price <= 1.0:
        raise ValueError(f"entry_price out of range: {entry_price}")
    return PX_FEE_RATE * (1.0 - entry_price)


def expected_fee_per_contract(entry_price: float,
                              win_prob: Optional[float] = None) -> float:
    """E[fee] in dollars for ONE contract bought at `entry_price`.

    `win_prob` defaults to entry_price -- on an exchange the price IS the
    probability. Pass an explicit win_prob only when you have a fair value that
    differs from the fill (e.g. measuring a mispriced leg), otherwise the
    expected fee is biased by exactly the edge you are trying to measure.
    """
    p = entry_price if win_prob is None else win_prob
    return p * PX_FEE_RATE * (1.0 - entry_price)


def kalshi_fee_per_contract(price: float, maker: bool = False,
                            series_charges_maker: bool = True) -> float:
    """Kalshi's marginal (un-rounded) per-contract fee, in dollars.

    Kept local so this module has no import cycle with kalshi_fees.py. Use
    kalshi_fees.fee_total() for the ACTUAL charge on a real order -- Kalshi
    rounds up to the cent on the aggregate, which costs up to 0.25c/contract
    extra on small clips (2.00c vs 1.75c at C=1, P=0.50).
    """
    if not maker:
        return KALSHI_TAKER_COEF * price * (1.0 - price)
    if not series_charges_maker:
        return 0.0
    return KALSHI_MAKER_COEF * price * (1.0 - price)


# ── the cross-venue corridor ─────────────────────────────────────────────────

def expected_loss_per_pair(kalshi_p: float, gap: float) -> float:
    """E[gross LOSING leg] per locked contract-pair, in dollars.

    Both legs settle: one to $1, one to $0. The winner books a gain, the loser
    books a full-size loss -- which is precisely why s165(d) hurts arbitrage so
    much more than a directional strategy.

        E[loss] = 2*P(1-P) - gap/2

    Derivation: with a = P - gap/2 on Kalshi and b = (1-P) - gap/2 on ProphetX,
    E[loss] = P*b + (1-P)*a = 2P(1-P) - gap/2.
    """
    return 2.0 * kalshi_p * (1.0 - kalshi_p) - gap / 2.0


def phantom_tax_per_pair(kalshi_p: float, gap: float,
                         tax_rate: float = 0.0) -> float:
    """Non-deductible-loss tax per contract-pair, in dollars.

    Zero if `tax_rate` is 0 -- i.e. under CAPITAL/s1256 characterization, where
    offsetting positions net normally. Non-zero only under GAMBLING
    characterization. That choice is UNSETTLED LAW with no IRS guidance, and it
    is worth more than any amount of code here: it moves the breakeven gap at
    P=0.50 from 2.25c to ~3.8c, which is the difference between the published
    2-4% cross-venue basis being an edge and being a loss.
    """
    if tax_rate <= 0.0:
        return 0.0
    return (1.0 - DEDUCTIBLE_LOSS_FRAC) * expected_loss_per_pair(
        kalshi_p, gap) * tax_rate


def breakeven_gap(kalshi_p: float, kalshi_maker: bool = False,
                  series_charges_maker: bool = True,
                  tax_rate: float = 0.0) -> float:
    """Minimum gross gap (dollars/contract-pair) for a lock to break even.

    Fees alone:      (0.07 or 0.0175 or 0) * P(1-P)  +  0.02 * P(1-P)
    With phantom tax, solving g = fees + 0.1*tau*(2P(1-P) - g/2):

        g = (fee_coef + 0.2*tau) * P(1-P) / (1 + 0.05*tau)

    At P=0.50, taker/taker, tau=0.32 this is 3.79c. The pre-registered gate in
    SPEC_P030 uses 3.85c -- the approximation that drops the -g/2 term. The
    0.06c difference is deliberately left in place as conservatism; the gate
    threshold is a fixed pre-registered constant and is NOT recomputed from
    this function, so that a later refinement here cannot loosen a gate
    after the fact.
    """
    k = kalshi_fee_per_contract(0.5, kalshi_maker, series_charges_maker) / 0.25
    coef = k + PX_FEE_RATE
    base = coef * kalshi_p * (1.0 - kalshi_p)
    if tax_rate <= 0.0:
        return base
    extra = 0.2 * tax_rate * kalshi_p * (1.0 - kalshi_p)
    return (base + extra) / (1.0 + 0.05 * tax_rate)


def no_cancel_ceiling(px_price: float) -> float:
    """Max edge (dollars) you may take on a ProphetX leg before the fill sits
    outside Rule 4.6's No-Cancellation Range and becomes bustable.

    Rule 4.6(b) lets "a Market Participant or third party" request the bust --
    your counterparty can void your good fill -- within 15 minutes, and that
    window is itself waivable at ProphetX's discretion. Rule 4.6(f) accepts
    "automated trading malfunction" as grounds, so someone ELSE's bot bug can
    cancel your leg. Determinations are final with no appeal.
    """
    return NO_CANCEL_FRAC * px_price


def corridor_table(tax_rate: float = 0.32, kalshi_maker: bool = False,
                   series_charges_maker: bool = True,
                   probs=(0.50, 0.55, 0.60, 0.65, 0.70,
                          0.75, 0.80, 0.85, 0.90, 0.95)) -> list:
    """[(kalshi_p, px_price, floor, ceiling, window)] all in CENTS.

    `window <= 0` means the corridor is CLOSED at that price: the smallest gap
    worth taking is already large enough to be bustable. Worst case -- assumes
    the entire gap sits on the ProphetX leg. If the gap is split across venues
    the ceiling binds less, so this is the conservative read.
    """
    rows = []
    for p in probs:
        px = 1.0 - p
        floor = breakeven_gap(p, kalshi_maker, series_charges_maker, tax_rate)
        ceil = no_cancel_ceiling(px)
        rows.append((p, px * 100, floor * 100, ceil * 100,
                     (ceil - floor) * 100))
    return rows


def rebate_rate(monthly_make_volume: float) -> float:
    """Effective (blended) rebate fraction of fees paid. Reference only --
    see the module docstring on the ~2026-08-29 expiry. Marginal by tier."""
    if monthly_make_volume <= 0:
        return 0.0
    earned, prev = 0.0, 0.0
    for cap, rate in REBATE_TIERS:
        band = min(monthly_make_volume, cap) - prev
        if band <= 0:
            break
        earned += band * rate
        prev = cap
    return earned / monthly_make_volume
