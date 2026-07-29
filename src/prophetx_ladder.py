"""ProphetX price ladder + American-odds <-> probability conversion.

ProphetX prices on an AMERICAN-ODDS INTEGER LADDER, not a cent grid. Orders
must snap to a valid tick or they are rejected. The authoritative source is
`GET /v4/mm/get_price_ladder`, which requires auth; the constant below is the
first-party fallback ladder shipped in ProphetX's own integration guide
(github.com/betprophet1/mm-api-integration-guide, src/constants.py), captured
2026-07-29.

VERIFIED PROPERTIES (asserted in tests/test_prophetx_ladder.py):
  * 291 ticks, symmetric, -10000 .. +10000
  * implied probability 0.9901% .. 99.0099%
  * tick size in probability points: MIN 0.1684pp, MAX 0.7576pp

That last line is the one that matters for cross-venue work, and it settles a
question that could have killed the whole thesis: **the ProphetX ladder is
finer than Kalshi's 1-cent grid at every price level.** The worst case (0.76pp
in the sparse 5-10% band) is still finer than Kalshi's 1.00pp. So Kalshi is
the binding grid and a 1-cent-precision cross-venue lock is expressible.
Snap error is at most half a tick, ~0.38pp worst case.

There is NO gap at even money: the ladder runs ... -102, -101, +100, +101 ...
continuously, with +100 == -100 == 50.000%. Note the ladder contains +100 but
not -100, so `snap_prob(0.5)` returns +100.

REFRESH THIS at runtime when credentials exist -- ProphetXClient.price_ladder()
prefers the live endpoint and falls back to this constant. A ladder that has
drifted silently would push orders to invalid ticks.
"""
from __future__ import annotations

from bisect import bisect_left
from typing import List, Optional

# fmt: off
VALID_ODDS: List[int] = [
    -10000, -7500, -5000, -4500, -4000, -3500, -3000, -2750, -2500, -2250, -2000, -1900,
    -1800, -1700, -1600, -1500, -1400, -1300, -1200, -1100, -1000, -980, -960, -940,
    -920, -900, -880, -860, -840, -820, -800, -780, -760, -740, -720, -700,
    -680, -660, -640, -620, -600, -580, -560, -540, -520, -500, -490, -480,
    -470, -460, -450, -440, -430, -420, -410, -400, -390, -380, -370, -360,
    -350, -340, -330, -320, -310, -300, -295, -290, -285, -280, -275, -270,
    -265, -260, -255, -250, -245, -240, -235, -230, -225, -220, -215, -210,
    -205, -200, -198, -196, -194, -192, -190, -188, -186, -184, -182, -180,
    -178, -176, -174, -172, -170, -168, -166, -164, -162, -160, -158, -156,
    -154, -152, -150, -148, -146, -144, -142, -140, -138, -136, -134, -132,
    -130, -128, -126, -124, -122, -120, -119, -118, -117, -116, -115, -114,
    -113, -112, -111, -110, -109, -108, -107, -106, -105, -104, -103, -102,
    -101, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110,
    111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 122, 124,
    126, 128, 130, 132, 134, 136, 138, 140, 142, 144, 146, 148,
    150, 152, 154, 156, 158, 160, 162, 164, 166, 168, 170, 172,
    174, 176, 178, 180, 182, 184, 186, 188, 190, 192, 194, 196,
    198, 200, 205, 210, 215, 220, 225, 230, 235, 240, 245, 250,
    255, 260, 265, 270, 275, 280, 285, 290, 295, 300, 310, 320,
    330, 340, 350, 360, 370, 380, 390, 400, 410, 420, 430, 440,
    450, 460, 470, 480, 490, 500, 520, 540, 560, 580, 600, 620,
    640, 660, 680, 700, 720, 740, 760, 780, 800, 820, 840, 860,
    880, 900, 920, 940, 960, 980, 1000, 1100, 1200, 1300, 1400, 1500,
    1600, 1700, 1800, 1900, 2000, 2250, 2500, 2750, 3000, 3500, 4000, 4500,
    5000, 7500, 10000,]
# fmt: on


def american_to_prob(odds: int | float) -> float:
    """Implied probability (0..1) of American odds. No vig removal -- this is
    the raw contract price, which on an exchange IS the probability."""
    o = float(odds)
    if o == 0:
        raise ValueError("American odds of 0 are not a price")
    return 100.0 / (o + 100.0) if o > 0 else (-o) / ((-o) + 100.0)


def prob_to_american(p: float) -> float:
    """Inverse of american_to_prob, unrounded (result is generally NOT a valid
    ladder tick -- use snap_prob for something orderable)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability out of range: {p}")
    # STRICT `>` at 0.5, not `>=`: the ladder contains +100 and NOT -100, so
    # p == 0.5 must map to +100 or roundtripping the even-money tick fails.
    return -100.0 * p / (1.0 - p) if p > 0.5 else 100.0 * (1.0 - p) / p


def _ladder_probs(ladder: Optional[List[int]] = None) -> List[tuple]:
    """[(prob, odds), ...] ascending by prob."""
    return sorted(((american_to_prob(o), o) for o in (ladder or VALID_ODDS)))


def snap_prob(p: float, ladder: Optional[List[int]] = None,
              direction: str = "nearest") -> tuple:
    """Snap a probability to the ladder. Returns (odds, snapped_prob).

    direction:
      "nearest" -- closest tick (use for marking/analysis)
      "down"    -- highest tick <= p. Use when BUYING: you may not pay more
                   than your model price, so round the price you pay downward.
      "up"      -- lowest tick >= p.

    "down"/"up" are the honest choices for edge measurement: snapping to
    "nearest" can manufacture up to half a tick (~0.38pp) of edge that is not
    actually orderable. `executable_gap` in prophetx_fees.py uses directional
    snapping for exactly this reason.
    """
    if direction not in ("nearest", "down", "up"):
        raise ValueError(f"unknown direction: {direction}")
    pairs = _ladder_probs(ladder)           # [(prob, odds), ...]
    probs = [x[0] for x in pairs]
    if p <= probs[0]:
        return pairs[0][1], probs[0]
    if p >= probs[-1]:
        return pairs[-1][1], probs[-1]
    i = bisect_left(probs, p)
    lo, hi = pairs[i - 1], pairs[i]
    if direction == "down":
        chosen = hi if hi[0] == p else lo
    elif direction == "up":
        chosen = hi
    else:
        chosen = lo if (p - lo[0]) <= (hi[0] - p) else hi
    # NOTE the flip: _ladder_probs is (prob, odds) but this function's contract
    # is (odds, prob). Returning `chosen` directly was a real bug -- callers got
    # a probability where they expected orderable odds.
    return chosen[1], chosen[0]


def tick_span_pp(p: float, ladder: Optional[List[int]] = None) -> float:
    """Width in probability POINTS of the ladder interval containing p.
    Used to bound snap error when reporting a measured gap."""
    pairs = _ladder_probs(ladder)
    probs = [x[0] for x in pairs]
    if p <= probs[0] or p >= probs[-1]:
        return 0.0
    i = bisect_left(probs, p)
    return (probs[i] - probs[i - 1]) * 100.0
