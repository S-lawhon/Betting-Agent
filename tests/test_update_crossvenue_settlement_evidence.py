import json
from datetime import datetime, timezone

from scripts.update_crossvenue_settlement_evidence import update
from src.dislocation_cases import CaseLedger
from src.mlb_statsapi import MlbGame


UTC = timezone.utc
PAIR = "gemini_kalshi_mlb_moneyline"


class FakeMlb:
    def __init__(self):
        self.calls = 0

    def schedule(self, date_et):
        self.calls += 1
        assert date_et == "2026-08-02"
        return [MlbGame(
            game_pk=1, away_name="Washington Nationals",
            home_name="Atlanta Braves", away_abbr="WSH", home_abbr="ATL",
            start_utc="2026-08-02T17:35:00Z", status="Final",
            detailed_state="Final",
        )]


def test_update_joins_cases_and_reuses_terminal_cache(tmp_path):
    database = tmp_path / "cases.sqlite3"
    ledger = CaseLedger(database)
    ledger.upsert([{
        "case_id": "CV-1", "pair_id": PAIR,
        "match_key": "2026-08-02|braves|nationals",
        "last_seen": "2026-08-02T17:00:00Z", "status": "closed",
    }], updated_at=datetime(2026, 8, 2, 20, tzinfo=UTC))
    ledger.close()
    client = FakeMlb()
    kwargs = {
        "database": database, "cache_dir": tmp_path / "cache",
        "output": tmp_path / "evidence.json", "pair_id": PAIR,
        "client": client, "now": datetime(2026, 8, 3, 12, tzinfo=UTC),
    }

    first = update(**kwargs)
    second = update(**kwargs)

    assert client.calls == 1
    assert first["status"] == "healthy"
    assert first["schedule_coverage"] == 1.0
    assert first["observed_exception_rate_lower_bound"] == 0.0
    assert second == json.loads((tmp_path / "evidence.json").read_text())
