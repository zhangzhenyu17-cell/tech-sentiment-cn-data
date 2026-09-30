from __future__ import annotations

from tech_sentiment.cnindex_index_price import fetch_cnindex_history


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "code": 200,
            "data": {
                "indexCode": "399673",
                "indexName": "创业板50",
                "indexEName": "ChiNext 50",
                "item": [
                    "timestamp",
                    "current",
                    "high",
                    "open",
                    "low",
                    "close",
                    "chg",
                    "percent",
                    "amount",
                    "volume",
                    "avg",
                ],
                "data": [
                    [
                        1704124800000,
                        1739.4195,
                        1775.17,
                        1775.17,
                        1739.0894,
                        1739.4195,
                        -39.061,
                        -0.0219631309,
                        33409321608.76,
                        833864786,
                        None,
                    ],
                    [
                        1704211200000,
                        1719.4607,
                        1739.6444,
                        1733.4766,
                        1709.9203,
                        1719.4607,
                        -19.9588,
                        -0.0114744028,
                        32053480936.58,
                        796611177,
                        None,
                    ],
                ],
            },
        }


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, headers: dict, timeout: float):
        self.calls.append(
            {"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout}
        )
        return FakeResponse()


def test_cnindex_daily_history_preserves_price_and_explicitly_missing_valuation() -> None:
    session = FakeSession()
    out = fetch_cnindex_history(
        "399673",
        start_date="2024-01-02",
        end_date="2024-01-03",
        session=session,
        retries=0,
    )
    assert len(session.calls) == 1
    assert session.calls[0]["params"] == {
        "indexCode": "399673",
        "startDate": "2024-01-02",
        "endDate": "2024-01-03",
    }
    assert list(out["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]
    assert list(out["close"]) == [1739.4195, 1719.4607]
    assert list(out["pct_chg"].round(6)) == [-2.196313, -1.14744]
    assert out["rolling_pe"].isna().all()
    assert set(out["valuation_source_state"]) == {
        "OFFICIAL_HISTORICAL_VALUATION_UNAVAILABLE"
    }
    assert set(out["provider"]) == {"cnindex:official_market_daily"}


def test_cnindex_identity_drift_fails_closed() -> None:
    class DriftResponse(FakeResponse):
        def json(self) -> dict:
            payload = super().json()
            payload["data"]["indexCode"] = "399006"
            return payload

    class DriftSession(FakeSession):
        def get(self, url: str, *, params: dict, headers: dict, timeout: float):
            return DriftResponse()

    try:
        fetch_cnindex_history(
            "399673",
            start_date="2024-01-02",
            end_date="2024-01-03",
            session=DriftSession(),
            retries=0,
        )
    except RuntimeError as exc:
        assert "returned indexCode" in str(exc.__cause__)
    else:
        raise AssertionError("CNINDEX identity drift should fail closed")
