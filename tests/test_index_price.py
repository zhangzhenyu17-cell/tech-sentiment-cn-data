from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.index_price import fetch_index_history, merge_index_price_rail


def _english_index_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-06-01", "2026-06-02", "2026-06-03"],
            "open": [1000.0, 1010.0, 1005.0],
            "close": [1010.0, 1005.0, 1020.0],
            "high": [1015.0, 1012.0, 1025.0],
            "low": [995.0, 1000.0, 1002.0],
            "volume": [100000, 110000, 120000],
            "amount": [1.0e9, 1.1e9, 1.2e9],
        }
    )


class FakeAKShare:
    def __init__(self, *, fail_tencent_attempts: int = 0):
        self.fail_tencent_attempts = fail_tencent_attempts
        self.tencent_calls: list[dict] = []
        self.legacy_calls: list[dict] = []

    def stock_zh_a_hist_tx(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str,
        timeout: float,
    ):
        self.tencent_calls.append(
            {
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "adjust": adjust,
                "timeout": timeout,
            }
        )
        if len(self.tencent_calls) <= self.fail_tencent_attempts:
            raise ConnectionError("temporary Tencent disconnect")
        return _english_index_frame()

    def index_zh_a_hist(
        self,
        *,
        symbol: str,
        period: str,
        start_date: str,
        end_date: str,
    ):
        self.legacy_calls.append(
            {
                "symbol": symbol,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        return pd.DataFrame(
            {
                "日期": ["2026-06-01", "2026-06-02", "2026-06-03"],
                "开盘": [1000.0, 1010.0, 1005.0],
                "收盘": [1010.0, 1005.0, 1020.0],
                "最高": [1015.0, 1012.0, 1025.0],
                "最低": [995.0, 1000.0, 1002.0],
                "成交量": [100000, 110000, 120000],
                "成交额": [1.0e9, 1.1e9, 1.2e9],
                "涨跌幅": [1.0, -0.5, 1.49],
            }
        )


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        return FakeResponse(
            {
                "data": {
                    "klines": [
                        "2026-06-01,1000,1010,1015,995,100000,1000000000,2.0,1.0,10,0",
                        "2026-06-02,1010,1005,1012,1000,110000,1100000000,1.2,-0.5,-5,0",
                        "2026-06-03,1005,1020,1025,1002,120000,1200000000,2.3,1.49,15,0",
                    ]
                }
            }
        )


def test_fetch_index_history_normalizes_legacy_akshare_contract_for_unknown_code() -> None:
    client = FakeAKShare()
    out = fetch_index_history(
        "000300",
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=client,
    )

    assert client.legacy_calls[0]["symbol"] == "000300"
    assert list(out["index_code"].unique()) == ["000300"]
    assert out["date"].dtype.kind == "M"
    assert list(out["close"]) == [1010.0, 1005.0, 1020.0]
    assert list(out["pct_chg"]) == [1.0, -0.5, 1.49]
    assert set(out["provider"]) == {"akshare:index_zh_a_hist"}


@pytest.mark.parametrize(
    ("code", "expected_symbol"),
    [("000688", "sh000688"), ("000985", "sh000985"), ("399673", "sz399673")],
)
def test_fetch_index_history_uses_tencent_for_formal_indices(
    code: str, expected_symbol: str
) -> None:
    client = FakeAKShare()
    session = FakeSession()
    out = fetch_index_history(
        code,
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=client,
        session=session,
        retries=0,
    )

    assert len(client.tencent_calls) == 1
    assert client.tencent_calls[0]["symbol"] == expected_symbol
    assert client.tencent_calls[0]["adjust"] == ""
    assert session.calls == []
    assert list(out["close"]) == [1010.0, 1005.0, 1020.0]
    assert set(out["provider"]) == {"akshare:tencent_index"}


def test_fetch_index_history_retries_tencent_before_fallback() -> None:
    client = FakeAKShare(fail_tencent_attempts=1)
    session = FakeSession()
    sleeps: list[float] = []

    out = fetch_index_history(
        "000688",
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=client,
        session=session,
        retries=1,
        retry_backoff_seconds=0.25,
        sleep_fn=sleeps.append,
    )

    assert len(client.tencent_calls) == 2
    assert sleeps == [0.25]
    assert session.calls == []
    assert set(out["provider"]) == {"akshare:tencent_index"}


@pytest.mark.parametrize(
    ("code", "expected_secid"),
    [("000688", "1.000688"), ("000985", "1.000985")],
)
def test_fetch_index_history_falls_back_to_direct_eastmoney_after_tencent_failure(
    code: str, expected_secid: str
) -> None:
    client = FakeAKShare(fail_tencent_attempts=5)
    session = FakeSession()
    out = fetch_index_history(
        code,
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=client,
        session=session,
        retries=0,
        retry_backoff_seconds=0,
    )

    assert len(client.tencent_calls) == 1
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["secid"] == expected_secid
    assert session.calls[0]["params"]["beg"] == "20260601"
    assert session.calls[0]["params"]["end"] == "20260630"
    assert set(out["provider"]) == {"eastmoney:direct_index_kline"}


def test_merge_index_price_rail_is_date_exact() -> None:
    sentiment = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"]),
            "temperature": [50.0, 55.0, 60.0],
        }
    )
    prices = fetch_index_history(
        "000688",
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=FakeAKShare(),
    )
    merged = merge_index_price_rail(sentiment, prices)

    assert list(merged["index_close"]) == [1010.0, 1005.0, 1020.0]
    assert list(merged["temperature"]) == [50.0, 55.0, 60.0]


def test_merge_index_price_rail_fails_closed_on_missing_date() -> None:
    sentiment = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-04"]),
            "temperature": [50.0, 55.0, 60.0],
        }
    )
    prices = fetch_index_history(
        "000688",
        start_date="2026-06-01",
        end_date="2026-06-30",
        client=FakeAKShare(),
    )

    with pytest.raises(ValueError, match="missing sentiment dates"):
        merge_index_price_rail(sentiment, prices, require_complete=True)

