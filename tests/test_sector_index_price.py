from __future__ import annotations

from tech_sentiment.sector_index_price import fetch_sector_index_history_direct


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": {
                "code": "931152",
                "klines": [
                    "2019-04-22,1000,1010,1015,995,100000,1000000000,2.0,1.0,10,0",
                    "2019-04-23,1010,1005,1012,1000,110000,1100000000,1.2,-0.5,-5,0",
                ],
            }
        }


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        return FakeResponse()


def test_931152_uses_fixed_eastmoney_identifier_without_discovery() -> None:
    session = FakeSession()
    out = fetch_sector_index_history_direct(
        "931152",
        start_date="2019-04-22",
        end_date="2019-04-30",
        session=session,
        retries=0,
    )
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["secid"] == "1.931152"
    assert session.calls[0]["params"]["beg"] == "20190422"
    assert session.calls[0]["params"]["end"] == "20190430"
    assert list(out["index_code"].unique()) == ["931152"]
    assert list(out["close"]) == [1010, 1005]
    assert set(out["provider"]) == {"eastmoney:direct_sector_index_kline"}
    assert set(out["provider_identifier"]) == {"1.931152"}


def test_unknown_sector_index_fails_closed() -> None:
    try:
        fetch_sector_index_history_direct(
            "999999",
            start_date="2019-04-22",
            end_date="2019-04-30",
            session=FakeSession(),
            retries=0,
        )
    except ValueError as exc:
        assert "no fixed EastMoney secid" in str(exc)
    else:
        raise AssertionError("unknown sector index should fail closed")
