import pandas as pd
import pytest
import requests

from tech_sentiment.data_akshare import (
    download_universe_history,
    fetch_current_csindex_universe,
    fetch_stock_history,
)


class FakeAKShare:
    def __init__(self):
        self.last_tencent_symbol = None
        self.eastmoney_timeouts: list[float] = []
        self.tencent_timeouts: list[float] = []

    def index_stock_cons_csindex(self, symbol: str):
        if symbol == "000688":
            return pd.DataFrame(
                {
                    "成分券代码": ["688001", "300750"],
                    "成分券名称": ["示例科创", "示例创业"],
                }
            )
        if symbol == "931000":
            return pd.DataFrame(
                {
                    "成分券代码": ["300750", "600000"],
                    "成分券名称": ["示例创业", "示例主板"],
                }
            )
        return pd.DataFrame()


    def index_stock_cons_weight_csindex(self, symbol: str):
        base = FakeAKShare.index_stock_cons_csindex(self, symbol)
        if base.empty:
            return base
        out = base.copy()
        out["权重"] = 1.0
        return out

    def stock_zh_a_hist(
        self,
        *,
        symbol: str,
        period: str,
        start_date: str,
        end_date: str,
        adjust: str,
        timeout: float,
    ):
        self.eastmoney_timeouts.append(timeout)
        if symbol in {"600000", "688999"}:
            raise RuntimeError("eastmoney failure")
        return pd.DataFrame(
            {
                "日期": ["2026-01-02", "2026-01-05"],
                "开盘": [10.0, 10.2],
                "收盘": [10.1, 10.3],
                "最高": [10.4, 10.5],
                "最低": [9.9, 10.0],
                "涨跌幅": [1.0, 1.98],
                "成交额": [1000000, 1200000],
                "换手率": [1.2, 1.4],
            }
        )

    def stock_zh_a_hist_tx(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str,
        timeout: float,
    ):
        self.last_tencent_symbol = symbol
        self.tencent_timeouts.append(timeout)
        if symbol.endswith("688999"):
            raise RuntimeError("tencent failure")
        return pd.DataFrame(
            {
                "date": ["2026-01-02", "2026-01-05"],
                "open": [20.0, 20.2],
                "close": [20.0, 20.4],
                "high": [20.5, 20.6],
                "low": [19.8, 20.0],
                "turnover": [0.012, 0.014],
                "amount": [2000000, 2200000],
            }
        )


def test_fetch_current_csindex_universe_deduplicates_symbols():
    universe = fetch_current_csindex_universe(
        ["000688", "931000"], client=FakeAKShare()
    )

    assert list(universe["symbol"]) == ["300750", "600000", "688001"]
    row = universe[universe["symbol"] == "300750"].iloc[0]
    assert row["source_index"] == "000688,931000"
    assert row["board"] == "chinext"
    assert set(universe["universe_mode"]) == {"current_snapshot"}
    assert set(universe["snapshot_source"]) == {"csindex_cons_xls"}



class FlakyConstituentAKShare(FakeAKShare):
    def __init__(self, *, failures: int, exc: Exception):
        super().__init__()
        self.failures = failures
        self.exc = exc
        self.constituent_calls = 0

    def index_stock_cons_csindex(self, symbol: str):
        self.constituent_calls += 1
        if self.constituent_calls <= self.failures:
            raise self.exc
        return super().index_stock_cons_csindex(symbol)


def test_fetch_current_csindex_universe_retries_transient_request_failures():
    client = FlakyConstituentAKShare(
        failures=2,
        exc=requests.exceptions.ChunkedEncodingError("incomplete read"),
    )
    universe = fetch_current_csindex_universe(
        ["000688"],
        retries=2,
        retry_backoff_seconds=0,
        client=client,
    )

    assert client.constituent_calls == 3
    assert set(universe["symbol"]) == {"688001", "300750"}


def test_fetch_current_csindex_universe_does_not_retry_non_transport_errors():
    client = FlakyConstituentAKShare(
        failures=1,
        exc=RuntimeError("schema/provider failure"),
    )
    with pytest.raises(RuntimeError, match="schema/provider failure"):
        fetch_current_csindex_universe(
            ["000688"],
            retries=3,
            retry_backoff_seconds=0,
            client=client,
        )
    assert client.constituent_calls == 1


def test_fetch_current_csindex_universe_retry_arguments_fail_closed():
    with pytest.raises(ValueError, match="retries"):
        fetch_current_csindex_universe(
            ["000688"], retries=-1, client=FakeAKShare()
        )
    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        fetch_current_csindex_universe(
            ["000688"], retry_backoff_seconds=-0.1, client=FakeAKShare()
        )


class MalformedPrimaryAKShare(FakeAKShare):
    def __init__(self):
        super().__init__()
        self.primary_calls = 0
        self.fallback_calls = 0

    def index_stock_cons_csindex(self, symbol: str):
        self.primary_calls += 1
        raise ValueError(
            "Excel file format cannot be determined, you must specify an engine manually."
        )

    def index_stock_cons_weight_csindex(self, symbol: str):
        self.fallback_calls += 1
        return FakeAKShare.index_stock_cons_weight_csindex(self, symbol)


class UnrelatedValueErrorPrimaryAKShare(FakeAKShare):
    def __init__(self):
        super().__init__()
        self.fallback_calls = 0

    def index_stock_cons_csindex(self, symbol: str):
        raise ValueError("unexpected constituent schema")

    def index_stock_cons_weight_csindex(self, symbol: str):
        self.fallback_calls += 1
        return FakeAKShare.index_stock_cons_weight_csindex(self, symbol)


def test_fetch_current_csindex_universe_uses_official_closeweight_fallback_for_malformed_xls():
    client = MalformedPrimaryAKShare()
    universe = fetch_current_csindex_universe(
        ["000688"],
        retries=2,
        retry_backoff_seconds=0,
        client=client,
    )

    assert client.primary_calls == 1
    assert client.fallback_calls == 1
    assert set(universe["symbol"]) == {"688001", "300750"}
    assert set(universe["snapshot_source"]) == {"csindex_closeweight_xls"}


def test_fetch_current_csindex_universe_does_not_fallback_on_unrelated_value_error():
    client = UnrelatedValueErrorPrimaryAKShare()
    with pytest.raises(ValueError, match="unexpected constituent schema"):
        fetch_current_csindex_universe(
            ["000688"],
            retries=2,
            retry_backoff_seconds=0,
            client=client,
        )
    assert client.fallback_calls == 0

def test_fetch_stock_history_normalizes_eastmoney_columns_and_timeout():
    client = FakeAKShare()
    prices = fetch_stock_history(
        "688001",
        start_date="2026-01-01",
        end_date="2026-01-31",
        timeout_seconds=7.5,
        client=client,
    )

    assert client.eastmoney_timeouts == [7.5]
    assert list(prices["symbol"].unique()) == ["688001"]
    assert prices["date"].dtype.kind == "M"
    assert prices.loc[0, "pct_chg"] == 1.0
    assert prices.loc[0, "board"] == "star"
    assert set(prices["provider"]) == {"eastmoney"}


def test_tencent_fallback_derives_percentage_change():
    client = FakeAKShare()
    prices = fetch_stock_history(
        "600000",
        start_date="2026-01-01",
        end_date="2026-01-31",
        provider="tencent",
        timeout_seconds=8.0,
        client=client,
    )

    assert client.tencent_timeouts == [8.0]
    assert pd.isna(prices.loc[0, "pct_chg"])
    assert round(prices.loc[1, "pct_chg"], 6) == 2.0
    assert set(prices["provider"]) == {"tencent"}


def test_tencent_uses_explicit_shanghai_prefix_for_689_series():
    client = FakeAKShare()
    prices = fetch_stock_history(
        "689009",
        start_date="2026-01-01",
        end_date="2026-01-31",
        provider="tencent",
        client=client,
    )

    assert client.last_tencent_symbol == "sh689009"
    assert list(prices["symbol"].unique()) == ["689009"]


def test_download_universe_history_falls_back_and_records_total_failures():
    universe = pd.DataFrame({"symbol": ["688001", "600000", "688999"]})
    client = FakeAKShare()
    result = download_universe_history(
        universe,
        start_date="2026-01-01",
        end_date="2026-01-31",
        retries=0,
        sleep_seconds=0,
        timeout_seconds=9.0,
        client=client,
    )

    assert client.eastmoney_timeouts == [9.0, 9.0, 9.0]
    assert client.tencent_timeouts == [9.0, 9.0]
    assert set(result.prices["symbol"]) == {"688001", "600000"}
    providers = result.prices.groupby("symbol")["provider"].first().to_dict()
    assert providers == {"600000": "tencent", "688001": "eastmoney"}
    assert list(result.errors["symbol"]) == ["688999"]
    error = result.errors.loc[0, "error"]
    assert "eastmoney" in error and "tencent" in error


def test_stock_history_timeout_must_be_positive():
    try:
        fetch_stock_history(
            "688001",
            start_date="2026-01-01",
            end_date="2026-01-31",
            timeout_seconds=0,
            client=FakeAKShare(),
        )
    except ValueError as exc:
        assert "timeout_seconds" in str(exc)
    else:
        raise AssertionError("non-positive timeout should fail closed")
