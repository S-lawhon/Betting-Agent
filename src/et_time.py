"""Eastern-time helpers for Kalshi ticker handling.

WHY THIS EXISTS
---------------
Kalshi sports tickers encode the event's start in ET WALL-CLOCK time, e.g.

    KXMLBGAME-26JUL121340CLEMIA-MIA
              ^^^^^^^^^^
              26 JUL 12, 13:40 ET

Converting that to UTC is not a fixed offset. The codebase repeatedly wrote
`+ timedelta(hours=4)`, which is EDT and is wrong by an hour from the first
Sunday in November to the second Sunday in March (2026: EDT ends Nov 1). The
droplet runs in UTC and the dev Mac runs in CT, so nothing about the host
timezone helps either — the offset has to come from the tz database.

Use `parse_mlb_ticker_start()` rather than hand-rolling the arithmetic.

MLB EXPOSURE NOTE: the regular season runs Mar-Sep and the postseason ends in
early November, so a fixed +4 is *usually* right for baseball. It is not right
for a World Series game played after Nov 1, and it is not right for any
non-baseball series that uses the same encoding.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:               # pragma: no cover - host without tzdata
    ET = None

UTC = timezone.utc

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# 26JUL121340 -> yy=26 mon=JUL dd=12 hh=13 mm=40
_TICKER_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")


def et_zone():
    """The ET tzinfo, or a fixed UTC-4 fallback if tzdata is unavailable.

    The fallback reproduces the old EDT-only behaviour instead of raising, so
    a missing tzdata degrades to the previous (imperfect) result rather than
    taking a job down.
    """
    return ET if ET is not None else timezone(timedelta(hours=-4))


def et_now() -> datetime:
    """Current time in ET, regardless of host timezone."""
    return datetime.now(UTC).astimezone(et_zone())


def et_today_str() -> str:
    """Today's date in ET as YYYY-MM-DD.

    This is the correct 'slate date' for MLB: a 22:10 ET first pitch is still
    *today's* game even though it is already tomorrow in UTC.
    """
    return et_now().strftime("%Y-%m-%d")


def et_date_tag(at: Optional[datetime] = None) -> str:
    """The ET date as it appears in a Kalshi ticker, e.g. '26JUL12'."""
    dt = at.astimezone(et_zone()) if at is not None else et_now()
    return dt.strftime("%y%b%d").upper()


def parse_mlb_ticker_start(ticker: str) -> Optional[datetime]:
    """UTC start time encoded in a Kalshi MLB ticker, or None if unparseable.

    The ticker fragment is ET wall-clock, so it is interpreted in
    America/New_York and then converted — never offset by a constant.

        >>> parse_mlb_ticker_start("KXMLBGAME-26JUL121340CLEMIA-MIA")
        datetime.datetime(2026, 7, 12, 17, 40, tzinfo=datetime.timezone.utc)
    """
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    m = _TICKER_RE.match(parts[1])
    if not m:
        return None
    yy, mon, dd, hh, mm = m.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        local = datetime(2000 + int(yy), month, int(dd), int(hh), int(mm),
                         tzinfo=et_zone())
    except ValueError:          # impossible date, e.g. 26FEB31
        return None
    return local.astimezone(UTC)
