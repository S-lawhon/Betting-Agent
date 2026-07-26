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
#   golf 2026-07-19; MLB 2026-07-20; round-leader + stat-leader 2026-07-26 via
#   GET /trade-api/v2/series/?category=Sports&limit=200  (per-series `fee_type`)
# Re-verify with that call before adding entries — this table has drifted twice.
#
# 2026-07-26 sweep of ALL 7,665 series in EVERY category: 88 tickers contain
# "LEAD" and not one is `quadratic_with_maker_fees`. Leader markets — round
# leader, season stat leader — appear to be uniformly maker-free.
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
    # ---- Round-based top-N, and the non-PGA make-cut/top-N (added 2026-07-26) ----
    # Found by P-023c and P-023 Phase 2. `KXPGATOP` above does NOT cover
    # `KXPGAR1TOP5` — the shared prefix is only "KXPGA", which charges — so every
    # round-based top-N fell through to the charging default. Likewise `KXLIVTOUR`
    # is not a prefix of `KXLIVTOP5`, and nothing covered KXDPWORLDTOURMAKECUT.
    # All verified `fee_type=quadratic` against GET /series?category=Sports on
    # 2026-07-26 (3,005 series swept; every KXPGAR{1,2,3}TOP*, KXLIVTOP* and
    # KXDPWORLDTOURMAKECUT returned quadratic, no exceptions).
    "KXPGAR1TOP": False,
    "KXPGAR2TOP": False,
    "KXPGAR3TOP": False,
    "KXLIVTOP": False,            # longer than KXLIVTOUR? No — disjoint. Both needed.
    "KXDPWORLDTOURMAKECUT": False,
    # ---- Round-leader series on the NON-PGA tours (added 2026-07-26) ----
    # These used to fall through to the charging default, costing P-022 a
    # phantom 0.0175*P*(1-P) — 0.129c/ct at P=0.08 — on the tours that supply
    # most of its tournaments. Full tickers, NOT a short "KXLIVR"-style prefix:
    # the PGA case proves short prefixes are unsafe, since "KXPGAR" would also
    # swallow KXPGARYDER, the one golf series that genuinely charges.
    "KXDPWORLDTOURR1LEAD": False,
    "KXDPWORLDTOURR2LEAD": False,
    "KXDPWORLDTOURR3LEAD": False,
    "KXLIVR1LEAD": False,
    "KXLIVR2LEAD": False,
    "KXLIVR3LEAD": False,
    "KXLPGAR1LEAD": False,
    "KXLPGAR2LEAD": False,
    "KXLPGAR3LEAD": False,
    "KXCHAMPTOURR1LEAD": False,   # longer than KXCHAMPTOUR (True) — wins
    # R2/R3 were NOT live on 2026-07-26 (the Champions Tour lists R1 only).
    # Pre-registered on the verified family pattern so a mid-season launch
    # cannot silently reintroduce the phantom fee; inert until they exist.
    "KXCHAMPTOURR2LEAD": False,
    "KXCHAMPTOURR3LEAD": False,
    # ---- MLB: game//league outcome series charge makers ----
    "KXMLB": True,            # league-level (also the conservative MLB default)
    "KXMLBGAME": True,
    "KXMLBAL": True,
    "KXMLBNL": True,
    "KXMLBASGAME": True,
    "KXMLBHRDERBY": True,     # longer than KXMLBHR, so it wins — intentional
    "KXMLBHRDERBYR1LEAD": False,   # ...and longer still, so IT wins over the
    # derby. Found 2026-07-26: the derby's own round-leader market is
    # `quadratic` like every other *LEAD series, but it sat behind
    # KXMLBHRDERBY (True) and was being charged. Four alternating levels:
    # KXMLB(T) < KXMLBHR(F) < KXMLBHRDERBY(T) < KXMLBHRDERBYR1LEAD(F).
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
    # ---- Season stat-leader family, all sports (added 2026-07-26) ----
    # KXLEADERMLBWINS, KXLEADERNBAPTS, KXLEADERNFLSACKS, KXLEADERUCLGOALS...
    # 39 live series on 2026-07-26, every one `quadratic`. Safe as a single
    # family prefix: no entry above is a prefix of "KXLEADER" and "KXLEADER"
    # is a prefix of none of them, so it neither shadows nor is shadowed.
    # Relevant to P-026.
    "KXLEADER": False,
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
