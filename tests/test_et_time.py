"""Timezone-correctness tests.

Every case here exists because a hardcoded UTC-4 ("EDT forever") offset was
found in production code during the 2026-07-21 timezone audit. The tests that
matter are the ones dated after 2026-11-01, when EDT -> EST: those are the
cases the old arithmetic got wrong, and they are the reason a fixed offset is
never acceptable for a ticker that encodes wall-clock ET.
"""

from datetime import datetime, timedelta, timezone
from unittest import TestCase

from src.et_time import (
    et_date_tag,
    et_now,
    et_today_str,
    parse_mlb_ticker_start,
)

UTC = timezone.utc


class TestParseMlbTickerStart(TestCase):
    """A Kalshi ticker's time fragment is ET wall-clock, not UTC."""

    def test_summer_game_is_utc_minus_4(self):
        # 2026-07-12 13:40 ET is EDT -> 17:40 UTC. This is the case the old
        # `+ timedelta(hours=4)` got right, and it must not regress.
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26JUL121340CLEMIA-MIA"),
            datetime(2026, 7, 12, 17, 40, tzinfo=UTC),
        )

    def test_winter_game_is_utc_minus_5(self):
        # THE BUG. After 2026-11-01 the same wall-clock is EST -> 18:40 UTC.
        # The old code produced 17:40 UTC: one hour early.
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26NOV031340CLEMIA-MIA"),
            datetime(2026, 11, 3, 18, 40, tzinfo=UTC),
        )

    def test_old_fixed_offset_and_new_parse_agree_during_edt(self):
        """Behaviour is unchanged for every game in the existing CLV sample.

        The captured CLV log at the time of the fix was entirely July 2026, so
        this fix must be a no-op for it — the fix is forward-looking.
        """
        ticker = "KXMLBGAME-26JUL212040WSHCOL-WSH"
        legacy = datetime(2026, 7, 21, 20, 40, tzinfo=UTC) + timedelta(hours=4)
        self.assertEqual(parse_mlb_ticker_start(ticker), legacy)

    def test_dst_boundary_day_before_and_after(self):
        # 2026-11-01 is the EDT->EST flip (02:00 local). A 13:40 game on
        # Oct 31 is still EDT; the same time on Nov 1 is EST.
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26OCT311340AAABBB-AAA"),
            datetime(2026, 10, 31, 17, 40, tzinfo=UTC),
        )
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26NOV011340AAABBB-AAA"),
            datetime(2026, 11, 1, 18, 40, tzinfo=UTC),
        )

    def test_spring_forward_boundary(self):
        # 2026-03-08 is EST->EDT. Opening day sits just after it.
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26MAR071340AAABBB-AAA"),
            datetime(2026, 3, 7, 18, 40, tzinfo=UTC),
        )
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26MAR091340AAABBB-AAA"),
            datetime(2026, 3, 9, 17, 40, tzinfo=UTC),
        )

    def test_late_night_game_keeps_its_et_date(self):
        # 22:10 ET is already tomorrow in UTC — the ticker date must not move.
        self.assertEqual(
            parse_mlb_ticker_start("KXMLBGAME-26JUL212210SEALAA-SEA"),
            datetime(2026, 7, 22, 2, 10, tzinfo=UTC),
        )

    def test_returns_none_rather_than_raising_on_junk(self):
        """The old parser raised AttributeError on an unmatched regex."""
        for bad in ("", "NODASH", "KXMLBGAME-NOTADATE-MIA",
                    "KXMLBGAME-26XXX121340AB-AB", "KXMLBGAME-26FEB311340AB-AB"):
            self.assertIsNone(parse_mlb_ticker_start(bad), bad)


class TestEtHelpers(TestCase):

    def test_et_now_is_aware_and_eastern(self):
        now = et_now()
        self.assertIsNotNone(now.tzinfo)
        # ET is UTC-4 or UTC-5, never anything else.
        self.assertIn(now.utcoffset(), (timedelta(hours=-4), timedelta(hours=-5)))

    def test_et_today_str_shape(self):
        self.assertRegex(et_today_str(), r"^\d{4}-\d{2}-\d{2}$")

    def test_date_tag_uses_et_not_utc_date(self):
        """01:00 UTC on Jul 22 is still Jul 21 in ET — the ticker says JUL21."""
        at = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
        self.assertEqual(et_date_tag(at), "26JUL21")

    def test_date_tag_same_day_when_no_rollover(self):
        at = datetime(2026, 7, 21, 18, 0, tzinfo=UTC)   # 14:00 ET
        self.assertEqual(et_date_tag(at), "26JUL21")

    def test_date_tag_winter_rollover(self):
        # 04:30 UTC Nov 5 is 23:30 EST Nov 4.
        at = datetime(2026, 11, 5, 4, 30, tzinfo=UTC)
        self.assertEqual(et_date_tag(at), "26NOV04")
