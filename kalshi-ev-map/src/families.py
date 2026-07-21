"""Functional market-family taxonomy over Kalshi series.

Families are defined by series-ticker patterns + category fallbacks, chosen to
match how a modeling effort would actually be organized (one model per family).
"""
import re

RULES = [
    # (family, regex on series_ticker) — first match wins
    ("crypto_15m",        r"^KX(BTC|ETH|SOL|XRP|DOGE)15M"),
    ("crypto_hourly",     r"^KX(BTC|ETH|SOL|XRP|DOGE)D$"),
    ("crypto_daily_range",r"^KX(BTC|ETH|SOL|XRP|DOGE)(RANGE|MAX|MIN)"),
    ("crypto_other",      r"^KX(BTC|ETH|SOL|XRP|DOGE|CRYPTO)"),
    ("index_intraday",    r"^KX(INX|NASDAQ100)(D|H|U|W)$"),
    ("index_yearly",      r"^KX(INX|NASDAQ100)Y"),
    ("forex_metals",      r"^KX(USDJPY|EURUSD|GBPUSD|GOLD|SILVER|OIL|WTI|CL)"),
    ("fed_rates",         r"^KX(FED|FEDDECISION|RATECUT|TNOTE|TBILL|HIKE|RHIKE)"),
    ("inflation",         r"^KXCPI"),
    ("labor_econ",        r"^KX(PAYROLLS|U3|JOBLESS|GDP|RECSSNBER)"),
    ("econ_other",        r"^KX(EGGS|GAS|AAAGASM|HOUSE|MORTGAGE|SSA|TARIFF)"),
    ("weather_temp",      r"^KX(HIGH|LOW)[A-Z]+$"),
    ("weather_other",     r"^KX(RAIN|SNOW|HURR|STORM|TORN|ACE)"),
    # sports: headline game/match winners
    ("sports_headline",   r"^KX(MLBGAME|NFLGAME|NBAGAME|NHLGAME|WNBAGAME|NCAAFGAME|UCLGAME|WCGAME|MLSGAME|LIGAMXGAME|EPLGAME|LALIGAGAME|SERIEAGAME|BUNDGAME|ATPMATCH|WTAMATCH|ATPCHALLENGERMATCH|T20MATCH|UFCFIGHT|BOXING|NASCAR.*RACE|F1RACE)"),
    # sports: in-game/prop/derivative markets
    ("sports_props",      r"^KX(MLB(TOTAL|SPREAD|STRIKEOUTS|HR|HITS|RBI|1ST|YRFI)|NFL(TOTAL|SPREAD|PROP)|NBA(TOTAL|SPREAD)|WC(SCORE|GOAL|AST|MOV|BTTS|1H|2H|CORNER|CARD|MESSI|FINISHING|SAVES|OFFSIDES)|UFC(MOV|ROUNDS|MOF)|T20(FIRST|TOTAL)|PGA(TOP|CUT)|NBASUMMER(TOTAL|SPREAD))"),
    # sports: futures/awards/season outcomes
    ("sports_futures",    r"^KX(MLB$|MLBAL|MLBNL|NBA$|NBAEAST|NBAWEST|NBAMVP|NBAROY|NFL[A-Z]*(CHAMP|EAST|NORTH|SOUTH|WEST|MVP|OTY)|SB$|NHL|NCAAF|MARMAD|MENWORLDCUP|WNBA$|HEISMAN|BALLONDOR|PGATOUR|PGARYDER|UCL$|LALIGA$|EPL$|NEXTTEAM|WCAWARD|WORLDCUP)"),
    ("mentions",          r"MENTION"),
    ("rotten_tomatoes",   r"^KXRT$"),
]

CATEGORY_FALLBACK = {
    "Sports": "sports_other",
    "Elections": "elections",
    "Politics": "politics",
    "Entertainment": "entertainment",
    "Crypto": "crypto_other",
    "Economics": "econ_other",
    "Financials": "financials_other",
    "Climate and Weather": "weather_other",
    "Mentions": "mentions",
    "Science and Technology": "tech_science",
    "Companies": "companies",
    "Commodities": "commodities",
    "World": "world",
    "Social": "social",
    "Education": "tech_science",
}

_compiled = [(fam, re.compile(pat)) for fam, pat in RULES]


def family_of(series_ticker, category=None):
    st = series_ticker or ""
    for fam, rx in _compiled:
        if rx.search(st):
            return fam
    return CATEGORY_FALLBACK.get(category, "other")


def add_family(df):
    df = df.copy()
    df["family"] = [family_of(s, c) for s, c in
                    zip(df["series_ticker"], df["category"])]
    return df
