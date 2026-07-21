"""Kalshi fee model.

Source: kalshi.com/docs/kalshi-fee-schedule.pdf, "Last updated and effective:
July 7, 2026" — downloaded and parsed 2026-07-18 (data/kalshi-fee-schedule.pdf).

General event contracts:
  taker fee = roundup(M_taker * 0.07   * C * P * (1-P))
  maker fee = roundup(M_maker * 0.0175 * C * P * (1-P))
  Rounding: fee + position cost rounds up to a centicent ($0.0001).
  M_taker defaults to 1; M_maker defaults to 0 (resting orders are FREE)
  except for the series in NON_STANDARD below (from the PDF's
  "Non-Standard Fees" table). A handful of series are entirely fee-free
  (taker multiplier 0). No settlement fee. Perps have a separate bps schedule
  (not modeled here).
"""
import math

TAKER_RATE = 0.07
MAKER_RATE = 0.0175

# series -> (maker_multiplier, taker_multiplier), from the July 7, 2026 PDF.
# Series NOT listed here: taker multiplier 1, maker multiplier 0.
NON_STANDARD = {
    # maker=1, taker=1 (both sides pay)
    **{s: (1, 1) for s in [
        "KXAAAGASM", "KXATPMATCH", "KXBALLONDOR", "KXBTCMAX150", "KXCPI",
        "KXCPIYOY", "KXEGGS", "KXEMMYCACTO", "KXEMMYCACTR", "KXEMMYCSERIES",
        "KXEMMYDACTO", "KXEMMYDACTR", "KXEMMYDSERIES", "KXFED",
        "KXFEDDECISION", "KXGDP", "KXHEISMAN", "KXINXY", "KXIPO", "KXLALIGA",
        "KXLLM1", "KXMARMAD", "KXMENWORLDCUP", "KXMLB", "KXMLBAL",
        "KXMLBASGAME", "KXMLBGAME", "KXMLBNL", "KXNASDAQ100Y", "KXNBA",
        "KXNBAEAST", "KXNBAMVP", "KXNBAROY", "KXNBAWEST", "KXNCAAF",
        "KXNCAAFACC", "KXNCAAFB10", "KXNCAAFB12", "KXNCAAFGAME",
        "KXNCAAFPLAYOFF", "KXNCAAFSEC", "KXNFLAFCCHAMP", "KXNFLAFCEAST",
        "KXNFLAFCNORTH", "KXNFLAFCSOUTH", "KXNFLAFCWEST", "KXNFLCOTY",
        "KXNFLCPOTY", "KXNFLDPOTY", "KXNFLDROTY", "KXNFLGAME", "KXNFLMVP",
        "KXNFLNFCCHAMP", "KXNFLNFCEAST", "KXNFLNFCNORTH", "KXNFLNFCSOUTH",
        "KXNFLNFCWEST", "KXNFLOPOTY", "KXNFLOROTY", "KXNHL", "KXNHLEAST",
        "KXNHLWEST", "KXPAYROLLS", "KXPGARYDER", "KXPGASOLHEIM", "KXPGATOUR",
        "KXRATECUTCOUNT", "KXSB", "KXSUPERBOWLHEADLINE", "KXU3", "KXUCL",
        "KXUCLGAME", "KXWCGAME", "KXWNBA", "KXWNBAGAME", "KXWTAMATCH",
    ]},
    # maker=0, taker=0 (fee-free novelty/promo series)
    **{s: (0, 0) for s in [
        "KXBTCY", "KXCITRINI", "KXDOED", "KXELECTIRAN", "KXETHY",
        "KXGAMBLINGREPEAL", "KXGREENLAND", "KXIRANDEMOCRACY",
        "KXLAYOFFSYINFO", "KXPAHLAVIHEAD",
    ]},
}


def multipliers(series_ticker):
    """(maker_mult, taker_mult) for a series."""
    return NON_STANDARD.get(series_ticker, (0, 1))


def fee(price, contracts=1, series_ticker=None, side="taker"):
    """Fee in dollars for a fill at `price` ($/contract)."""
    maker_m, taker_m = multipliers(series_ticker)
    rate, mult = ((TAKER_RATE, taker_m) if side == "taker"
                  else (MAKER_RATE, maker_m))
    raw = mult * rate * contracts * price * (1.0 - price)
    return math.ceil(raw * 10000) / 10000.0  # round up to centicent


def fee_per_contract(price, series_ticker=None, side="taker"):
    return fee(price, 1, series_ticker, side)


def edge_after_fees(model_p, fill_price, series_ticker=None, side="taker"):
    """Expected value per contract of buying YES at fill_price, net of fees."""
    return model_p - fill_price - fee_per_contract(fill_price, series_ticker, side)
