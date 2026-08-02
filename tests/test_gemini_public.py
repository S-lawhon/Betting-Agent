from src.gemini_public import GeminiPublic


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.headers = {}
        self.params = []

    def get(self, url, params=None, timeout=None):
        self.params.append(dict(params or {}))
        return _Response(next(self.payloads))


def test_active_prediction_events_paginates_by_offset():
    client = GeminiPublic(min_interval=0)
    session = _Session([
        {"data": [{"id": "one"}, {"id": "two"}],
         "pagination": {"total": 3}},
        {"data": [{"id": "three"}], "pagination": {"total": 3}},
    ])
    client._session = session

    rows = client.active_prediction_events(limit=2)

    assert [row["id"] for row in rows] == ["one", "two", "three"]
    assert [params["offset"] for params in session.params] == [0, 2]


def test_active_prediction_events_fails_closed_on_bad_shape():
    client = GeminiPublic(min_interval=0)
    client._session = _Session([{"unexpected": []}])

    try:
        client.active_prediction_events()
    except RuntimeError as exc:
        assert "no event list" in str(exc)
    else:
        raise AssertionError("bad response must not look like an empty market")
