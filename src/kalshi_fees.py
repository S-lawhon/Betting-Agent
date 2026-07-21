"""Kalshi fee model + net-edge gate.

Kalshi taker fee = round_up_to_cent(0.07 * C * P * (1-P)); maker = 0.0175 * ...
The P*(1-P) term peaks at P=0.5, so fees bite hardest on ~50c game contracts —
exactly where competitive moneylines cluster. Net edge MUST clear fee + spread.

SERIES-AWARE MAKER FEES (added 2026-07 for P-017 golf; MLB added 2026-07-20):
Not all series charge maker fees. Kalshi's `quadratic` fee_type charges
makers ZERO; `quadratic_with_maker_fees` charges makers the 0.0175 rate.
The split runs along props-vs-outcomes, not along sports: derivative series
(golf top-N/make-cut/H2H; MLB hits, K's, totals, total bases, HR, SB, RFI,
F5) are `quadratic` → zero maker fee, while game-winner and league-outcome
series (KXPGA, KXMLBGAME, KXMLB, ...) are `quadratic_with_maker_fees`.
Pass `series_ticker` to fee_per_contract / fee_total to get the correct maker
treatment. Backward compatible: with NO series_ticker, maker fees fall back to
the general 0.0175 rate. P-016 relies on that fallback (it makes on KXMLBGAME,
which does charge) and must not be perturbed while its gate sample is running.
"""
from __future__ import annotations
import math

TAKER_COEF = 0.07
MAKER_COEF = 0.0175

# Series family -> does it charge maker fees? True == "quadratic_with_maker_fees"
# (0.0175 maker rate), False == "quadratic" (ZERO maker fee).
#
# Matching is LONGEST-PREFIX-WINS, which is what makes the overlapping families
# resolve correctly: "KXMLB" (game winner, charges) is a prefix of "KXMLBHR"
# (home-run prop, free) which is in turn a prefix of "KXMLBHRDERBY" (charges).
# A naive `any(startswith(...))` over two flat tuples gets all three wrong.
#
# Verified against live /series metadata:
#   golf 2026-07-19; MLB 2026-07-20 via
#   GET /trade-api/v2/series/?category=Sports&limit=200  (per-series `fee_type`)
# Re-verify with that call before adding entries — this table has drifted twice.
_SERIES_MAKER_FEE: dict[str, bool] = {
    # ---- Golf: winner / outright series charge makers ----
    "KXPGA": True,            # PGA Championship winner
    "KXPGATOUR": True,
    "KXTHEOPEN": True,
    "KXPGARYDER": True,
    "KXPGASOLHEIM": True,
    "KXLPGATOUR": True,
    "KXLIVTOUR": True,
    "KXCHAMPTOUR": True,
    # ---- Golf: derivative/prop series are maker-free ----
    "KXPGATOP": False,
    "KXPGAMAKECUT": False,
    "KXPGAH2H": False,
    "KXGOLFH2H": False,
    "KXPGA3BALL": False,
    "KXPGA5BALL": False,
    "KXPGAR1LEAD": False,
    "KXPGAR2LEAD": False,
    "KXPGAR3LEAD": False,
    "KXPGAUNDERPAR": False,
    "KXDPWTH2H": False,
    "KXLIVH2H": False,
    "KXPGACUTLINE": False,
    # ---- MLB: game//league outcome series charge makers ----
    "KXMLB": True,            # league-level (also the conservative MLB default)
    "KXMLBGAME": True,
    "KXMLBAL": True,
    "KXMLBNL": True,
    "KXMLBASGAME": True,
    "KXMLBHRDERBY": True,     # longer than KXMLBHR, so it wins — intentional
    # ---- MLB: prop/derivative series are maker-free ----
    "KXMLBHIT": False,
    "KXMLBKS": False,
    "KXMLBTOTAL": False,
    "KXMLBSPREAD": False,
    "KXMLBTEAMTOTAL": False,
    "KXMLBTB": False,
    "KXMLBHR": False,
    "KXMLBHRR": False,
    "KXMLBSB": False,
    "KXMLBRFI": False,
    "KXMLBF5": False,
}


def series_maker_charges_fee(series_ticker: str) -> bool:
    """True if this series charges maker fees (quadratic_with_maker_fees).

    Prop/derivative series (golf top-N, make-cut, H2H; MLB hits, K's, totals,
    total bases, HR, HRR, SB, RFI, F5, spreads) return False → maker fee is
    zero. Game-winner and league-outcome series return True.

    Series tickers are matched by LONGEST prefix, so a market's full series
    ticker ("KXPGATOP20", "KXMLBHRR") resolves to its family. An unrecognised
    series returns True — conservative, since over-charging a fee only makes
    the net-edge gate stricter.
    """
    s = (series_ticker or "").upper()
    if not s:
        return True  # unknown → conservative (charge)
    best: tuple[int, bool] | None = None
    for prefix, charges in _SERIES_MAKER_FEE.items():
        if s.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), charges)
    return best[1] if best is not None else True


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
