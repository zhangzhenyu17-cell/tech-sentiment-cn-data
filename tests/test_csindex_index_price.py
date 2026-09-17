from __future__ import annotations

from tech_sentiment.csindex_index_price import fetch_csindex_history


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": [
                [
                    "2019-04-22",
                    "931152",
                    "中证创新药产业指数",
                    "创新药",
                    "CSI Innovative Drug Industry Index",
                    "CSI Innovative Drug",
                    "1000",
                    "1015",
                    "995",
                    "1010",
                    "10",
                    "1.0",
                    "100000",
                    "1000000000",
                    "28",
                    "30.5",
                ],
                [
                    "2019-04-23",
                    "931152",
                    "中证创新药产业指数",
                    "创新药",
                    "CSI Innovative Drug Industry Index",
                    "CSI Innovative Drug",
                    "1010",
                    "1012",
                    "1000",
                    "1005",
                    "-5",
                    "-0.5",
                    "110000",
                    "1100000000",
                    "28",
                    "30.2",
                ],
            ]
        }


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        return FakeResponse()


def test_official_csindex_array_rows_are_parsed_without_guessing() -> None:
    session = FakeSession()
    out = fetch_csindex_history(
        "931152",
        start_date="2019-04-22",
        end_date="2019-04-23",
        session=session,
        retries=0,
    )

    assert len(session.calls) == 1
    assert session.calls[0]["params"] == {
        "indexCode": "931152",
        "startDate": "20190422",
        "endDate": "20190423",
    }
    assert list(out["date"].dt.strftime("%Y-%m-%d")) == ["2019-04-22", "2019-04-23"]
    assert list(out["index_code"]) == ["931152", "931152"]
    assert list(out["close"]) == [1010, 1005]
    assert set(out["provider"]) == {"csindex:index_perf"}
    assert set(out["provider_identifier"]) == {"931152"}


def test_positional_schema_drift_fails_closed() -> None:
    class BadResponse(FakeResponse):
        def json(self) -> dict:
            return {"data": [["2019-04-22", "931152", "too-short"]]}

    class BadSession(FakeSession):
        def get(self, url: str, *, params: dict, timeout: float):
            return BadResponse()

    try:
        fetch_csindex_history(
            "931152",
            start_date="2019-04-22",
            end_date="2019-04-23",
            session=BadSession(),
            retries=0,
        )
    except ValueError as exc:
        assert "expected 16 fields" in str(exc)
    else:
        raise AssertionError("CSI positional schema drift should fail closed")
