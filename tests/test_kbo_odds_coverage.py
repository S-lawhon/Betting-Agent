from kbo_spread_research.check_odds_coverage import check


class Response:
    status_code = 200
    headers = {
        "x-requests-last": "10",
        "x-requests-used": "20",
        "x-requests-remaining": "80",
    }
    text = ""

    @staticmethod
    def json():
        return {
            "timestamp": "2026-08-16T09:45:00Z",
            "data": [{
                "bookmakers": [{
                    "key": "pinnacle",
                    "markets": [{
                        "key": "spreads",
                        "outcomes": [
                            {"name": "A", "point": -2.5, "price": 1.9},
                            {"name": "B", "point": 2.5, "price": 1.9},
                        ],
                    }],
                }],
            }],
        }


def test_coverage_summary_never_includes_api_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "secret-key")
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    result = check("2026-08-16T09:45:00Z", "eu")

    assert result["status"] == "available"
    assert result["events"] == 1
    assert result["bookmakers"] == {"pinnacle": 1}
    assert result["two_sided_spread_lines"] == 1
    assert "secret-key" not in str(result)
