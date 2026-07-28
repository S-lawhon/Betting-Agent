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
the general 0.0175 rate.

WHO DEPENDS ON THAT FALLBACK — corrected 2026-07-28.
This note used to name P-016 as the reason the fallback must not be perturbed.
**P-016's gate is CLOSED** (resolved 2026-07-21, verdict KILL; the unit is
inactive and disabled and has written no fill since 2026-07-22), so that
particular reason is dead — and reading it today would suggest the fallback is
now free to change. **It is not.** The fallback is still load-bearing for a
gate that IS running:

  * `scripts/clv_settlement.py` and `scripts/clv_backfill.py` compute
    `clv_net_maker` with no series_ticker, and that field is what
    `data/trade_logs/clv_log.jsonl` carries into **P-001's live CLV gate**.
  * `src/maker_challenger.py` and `scripts/maker_{report,diagnostics}.py` also
    call it bare.

So the rule survives its original justification: **do not perturb the general
maker fallback while P-001's CLV sample is accruing.** The owner changed; the
constraint did not.
"""
from __future__ import annotations
import json
import math
from functools import lru_cache
from pathlib import Path

TAKER_COEF = 0.07
MAKER_COEF = 0.0175

# ── The fee table is a GENERATED FIXTURE, not a hand-maintained dict ──
#
# `_SERIES_MAKER_FEE` used to live here as a hand-curated prefix table. It
# drifted FIVE times — four found in 2026-07 (round top-N, non-PGA make-cut,
# non-PGA round leaders, the KXMLBHRDERBYR1LEAD nesting) and a fifth found on
# 2026-07-28 by this very replacement: KXCHAMPTOUR, KXLIVTOUR and KXLPGATOUR
# were all marked as CHARGING and none of them does.
#
# A wrong maker fee never looks broken. It produces a plausible number shifted
# by a fraction of a cent, in exactly the band where these hypotheses live and
# die, and it flows straight into every backtest's net-edge gate.
#
# So the table is now generated from Kalshi's own `/series` — one call, 12,199
# series, each with a `fee_type` — by `scripts/generate_fee_fixture.py`, and
# drift against live Kalshi is detected by `scripts/check_fee_fixture.py`.
# Nothing here is hand-editable.
#
# Matching is EXACT on the series ticker, which kills the prefix trap outright.
# The trap was never intuitive: `KXPGATOP` does not cover `KXPGAR1TOP5` (their
# shared prefix is `KXPGA`, which charges), and `KXMLB` < `KXMLBHR` <
# `KXMLBHRDERBY` < `KXMLBHRDERBYR1LEAD` alternate charge/free/charge/free.
# Under exact matching none of that can happen.
_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "kalshi_series_fees.json"

# Series that do not exist on Kalshi yet, pre-registered on a family pattern
# that has been verified exhaustively, so a mid-season launch cannot silently
# reintroduce a phantom fee. This is NOT a general-purpose override table: an
# entry is only legitimate while its ticker is absent from the fixture, and
# `tests/test_kalshi_fees.py::test_pending_series_expire_once_kalshi_lists_them`
# fails as soon as one appears, forcing it to be deleted rather than left to
# rot into a second hand-maintained dict.
#
# 2026-07-26 sweep of all series in every category: 88 tickers contain "LEAD"
# and not one is `quadratic_with_maker_fees`. The Champions Tour lists R1 only.
_PENDING_SERIES: dict[str, bool] = {
    "KXCHAMPTOURR2LEAD": False,
    "KXCHAMPTOURR3LEAD": False,
}

@lru_cache(maxsize=1)
def _fixture() -> tuple[frozenset, frozenset]:
    """(all known series tickers, tickers charging maker fees).

    Loaded once. A missing or unreadable fixture yields empty sets, which
    makes every series 'unknown' and therefore CHARGED — the conservative
    direction, since over-charging only makes the net-edge gate stricter.
    """
    try:
        data = json.loads(_FIXTURE_PATH.read_text())
    except (OSError, ValueError):
        return frozenset(), frozenset()
    return (frozenset(data.get("all_series") or ()),
            frozenset(data.get("maker_fee_series") or ()))


def fixture_metadata() -> dict:
    """Provenance of the committed fee fixture (for reports and the CI check)."""
    try:
        data = json.loads(_FIXTURE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items()
            if k not in ("all_series", "maker_fee_series",
                         "zero_fee_multiplier_series")}


def series_maker_charges_fee(series_ticker: str) -> bool:
    """True if this series charges maker fees (quadratic_with_maker_fees).

    Matching is EXACT on the series ticker, tried in two steps:

      1. the string as given — because **148 series tickers contain a hyphen**
         (`KXMLBWINS-MIL`, `KXNFLWINS-SF`, `KXWO-GOLD`). Splitting first would
         truncate those to a segment that is a different series, or to nothing
         at all;
      2. failing that, the leading hyphen segment — because callers routinely
         pass a full market ticker ("KXPGATOP20-ROC26-TMOO").

    Step 2 is only safe because the two can never disagree: 86 hyphenated
    series have a leading segment that is also a series, and **zero of the 86
    differ on fee type**. `tests/test_fee_fixture_current.py` asserts that
    against the whole live inventory, so if Kalshi ever creates such a pair the
    suite fails rather than the fee silently going wrong.

    Three outcomes:
      * in the fixture's maker-fee list   -> True  (charges)
      * in the fixture but not that list  -> False (quadratic, maker-free)
      * not in the fixture at all         -> True  (conservative)

    The third case matters: a series listed AFTER the fixture was generated is
    unknown, and treating an unknown series as free would understate cost.
    Over-charging only makes the gate stricter. `scripts/check_fee_fixture.py`
    is what stops the unknown set from quietly growing.
    """
    raw = (series_ticker or "").upper()
    if not raw:
        return True                                   # unknown -> conservative
    known, charging = _fixture()
    for s in (raw, raw.split("-")[0]):
        if not s:
            continue
        if s in charging:
            return True
        if s in known:
            return False
        pending = _PENDING_SERIES.get(s)
        if pending is not None:
            return pending
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
