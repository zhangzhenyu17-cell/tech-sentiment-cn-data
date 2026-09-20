import pandas as pd

from tech_sentiment.capital_input_data import (
    SSE_ETF_SCALE_QUERY_URL,
    SSE_ETF_SCALE_SOA_QUERY_URL,
    SSE_ETF_SHARE_SOURCE_URL,
    SSE_TURNOVER_HISTORICAL_SQL_ID,
    SSE_TURNOVER_QUERY_URL,
    _fetch_sse_historical_daily_overview,
    _sse_historical_turnover_payload_frame,
    _fetch_sse_etf_scale_direct,
    _sse_etf_scale_payload_frame,
    combine_sse_szse_a_share_turnover,
    fetch_sse_szse_a_share_turnover_history,
    normalize_sse_a_share_turnover,
    normalize_sse_etf_share_snapshot,
    normalize_szse_a_share_turnover,
    qualify_financing_yuan,
    qualify_trailing_etf_coverage,
)


def test_etf_normalization_and_coverage_does_not_fill_missing_days():
    snapshot = pd.DataFrame({
        "基金代码": ["588000", "510300"],
        "统计日期": ["2026-09-16", "2026-09-16"],
        "基金份额": [9_000_000_000, 1_000_000_000],
    })
    normalized = normalize_sse_etf_share_snapshot(
        snapshot, observation_date="2026-09-16", fund_codes=["588000"]
    )
    assert normalized.loc[0, "fund_code"] == "588000"
    assert normalized.loc[0, "fund_shares"] == 9_000_000_000
    cal = pd.bdate_range("2026-06-01", periods=60)
    history = pd.DataFrame({
        "date": cal,
        "fund_code": "588000",
        "fund_shares": range(1, 61),
    }).drop(index=list(range(12)))
    coverage = qualify_trailing_etf_coverage(
        history, trading_dates=cal, fund_code="588000"
    )
    assert coverage.iloc[-1]["coverage"] == 0.8
    assert bool(coverage.iloc[-1]["eligible"])
    assert int(coverage["observed"].sum()) == 48




def test_sse_etf_scale_direct_normalizes_official_tot_vol_10k_shares():
    payload = {
        "result": [
            {
                "NUM": "1",
                "SEC_CODE": "588000",
                "SEC_NAME": "科创50ETF",
                "ETF_TYPE": "股票型",
                "STAT_DATE": "2026-09-17",
                "TOT_VOL": "900001.23",
            }
        ]
    }
    frame = _sse_etf_scale_payload_frame(payload)
    assert frame.loc[0, "基金代码"] == "588000"
    assert str(frame.loc[0, "统计日期"]) == "2026-09-17"
    assert frame.loc[0, "基金份额"] == 9_000_012_300.0


def test_sse_etf_scale_direct_uses_same_official_https_browser_fallback():
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, *, payload=None, json_error=None):
            self.url = SSE_ETF_SCALE_QUERY_URL
            self.status_code = 200
            self._payload = payload
            self._json_error = json_error

        def raise_for_status(self):
            return None

        def json(self):
            if self._json_error is not None:
                raise self._json_error
            return self._payload

    def plain_get(url, **kwargs):
        calls.append(("plain", url))
        return FakeResponse(json_error=ValueError("not json"))

    def browser_get(url, **kwargs):
        calls.append(("browser", url))
        assert kwargs["impersonate"] == "chrome"
        return FakeResponse(
            payload={
                "result": [
                    {
                        "NUM": "1",
                        "SEC_CODE": "588000",
                        "SEC_NAME": "科创50ETF",
                        "ETF_TYPE": "股票型",
                        "STAT_DATE": "2026-09-17",
                        "TOT_VOL": "900001.23",
                    }
                ]
            }
        )

    frame = _fetch_sse_etf_scale_direct(
        "20260917",
        plain_get=plain_get,
        browser_get=browser_get,
    )

    assert calls == [
        ("plain", SSE_ETF_SCALE_QUERY_URL),
        ("browser", SSE_ETF_SCALE_QUERY_URL),
    ]
    assert frame.loc[0, "基金代码"] == "588000"
    assert frame.loc[0, "基金份额"] == 9_000_012_300.0



def test_sse_etf_scale_direct_warms_same_official_browser_session_after_403():
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, url: str, *, payload=None, status_code: int = 200):
            self.url = url
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    def plain_get(url, **kwargs):
        calls.append(("plain", url))
        return FakeResponse(url, status_code=403)

    def browser_get(url, **kwargs):
        calls.append(("browser", url))
        return FakeResponse(url, status_code=403)

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(("session", url))
            if url == SSE_ETF_SHARE_SOURCE_URL:
                return FakeResponse(url, status_code=403)
            assert url == SSE_ETF_SCALE_QUERY_URL
            assert kwargs["headers"]["Referer"] == SSE_ETF_SHARE_SOURCE_URL
            assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
            return FakeResponse(
                url,
                payload={
                    "result": [
                        {
                            "NUM": "1",
                            "SEC_CODE": "588000",
                            "SEC_NAME": "科创50ETF",
                            "ETF_TYPE": "股票型",
                            "STAT_DATE": "2026-09-17",
                            "TOT_VOL": "900001.23",
                        }
                    ]
                },
            )

        def close(self):
            calls.append(("close", "session"))

    frame = _fetch_sse_etf_scale_direct(
        "20260917",
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=FakeSession,
    )

    assert calls == [
        ("plain", SSE_ETF_SCALE_QUERY_URL),
        ("browser", SSE_ETF_SCALE_QUERY_URL),
        ("session", SSE_ETF_SHARE_SOURCE_URL),
        ("session", SSE_ETF_SCALE_QUERY_URL),
        ("close", "session"),
    ]
    assert frame.loc[0, "基金代码"] == "588000"
    assert frame.loc[0, "基金份额"] == 9_000_012_300.0



def test_sse_etf_scale_uses_same_sql_on_official_soa_fallback_and_records_interface():
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, url: str, *, payload=None, status_code: int = 200, text: str = ""):
            self.url = url
            self._payload = payload
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    def plain_get(url, **kwargs):
        return FakeResponse(url, status_code=403)

    def browser_get(url, **kwargs):
        return FakeResponse(url, status_code=403)

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(("session", url))
            if url == SSE_ETF_SHARE_SOURCE_URL:
                return FakeResponse(url, status_code=200)
            if url == SSE_ETF_SCALE_QUERY_URL:
                return FakeResponse(url, status_code=403)
            assert url == SSE_ETF_SCALE_SOA_QUERY_URL
            assert kwargs["params"]["sqlId"] == "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
            assert kwargs["params"]["STAT_DATE"] == "2026-09-17"
            assert kwargs["params"]["isPagination"] == "false"
            return FakeResponse(
                url,
                payload={
                    "result": [
                        {
                            "NUM": "1",
                            "SEC_CODE": "588000",
                            "SEC_NAME": "科创50ETF",
                            "ETF_TYPE": "股票型",
                            "STAT_DATE": "2026-09-17",
                            "TOT_VOL": "900001.23",
                        }
                    ]
                },
            )

        def close(self):
            pass

    frame = _fetch_sse_etf_scale_direct(
        "20260917",
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=FakeSession,
    )
    normalized = normalize_sse_etf_share_snapshot(
        frame,
        observation_date="2026-09-17",
        fund_codes=["588000"],
    )

    assert ("session", SSE_ETF_SCALE_SOA_QUERY_URL) in calls
    assert normalized.loc[0, "provider_interface"] == SSE_ETF_SCALE_SOA_QUERY_URL
    assert normalized.loc[0, "fund_shares"] == 9_000_012_300.0


def test_sse_etf_scale_accepts_strict_jsonp_only_on_official_query_interface():
    class FakeResponse:
        def __init__(self, url: str, *, status_code: int = 200, text: str = ""):
            self.url = url
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            raise ValueError("jsonp")

    def plain_get(url, **kwargs):
        return FakeResponse(url, status_code=403)

    def browser_get(url, **kwargs):
        return FakeResponse(url, status_code=403)

    class FakeSession:
        def get(self, url, **kwargs):
            if url == SSE_ETF_SHARE_SOURCE_URL:
                return FakeResponse(url)
            if url == SSE_ETF_SCALE_SOA_QUERY_URL:
                return FakeResponse(url, status_code=403)
            if "jsonCallBack" in kwargs.get("params", {}):
                body = (
                    'jsonpCallbackETFScale({"result":[{"NUM":"1","SEC_CODE":"588000",'
                    '"SEC_NAME":"科创50ETF","ETF_TYPE":"股票型","STAT_DATE":"2026-09-17",'
                    '"TOT_VOL":"900001.23"}]});'
                )
                return FakeResponse(url, text=body)
            return FakeResponse(url, status_code=403)

        def close(self):
            pass

    frame = _fetch_sse_etf_scale_direct(
        "20260917",
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=FakeSession,
    )
    assert frame.loc[0, "基金代码"] == "588000"
    assert frame.attrs["provider_interface"] == SSE_ETF_SCALE_QUERY_URL

def test_sse_szse_turnover_normalization_matches_frozen_d1_scope():
    sse_raw = pd.DataFrame({
        "单日情况": ["成交金额"],
        "主板A": [3713.66],
        "主板B": [1.36],
        "科创板": [378.23],
    })
    szse_raw = pd.DataFrame({
        "证券类别": ["股票", "主板B股", "基金", "债券"],
        "成交金额": [500_100_000_000.0, 100_000_000.0, 50_000_000_000.0, 30_000_000_000.0],
    })
    sse = pd.DataFrame([
        normalize_sse_a_share_turnover(sse_raw, observation_date="2026-09-16")
    ])
    szse = pd.DataFrame([
        normalize_szse_a_share_turnover(szse_raw, observation_date="2026-09-16")
    ])
    combined = combine_sse_szse_a_share_turnover(sse, szse)
    expected_sse = (3713.66 + 378.23) * 100_000_000
    assert combined.loc[0, "sse_a_share_turnover_yuan"] == expected_sse
    assert combined.loc[0, "szse_a_share_turnover_yuan"] == 500_000_000_000
    assert combined.loc[0, "scope"] == "SSE_SZSE_A_SHARES"
    assert combined.loc[0, "canonical_all_a_state"] == "INCOMPLETE_BSE_NOT_INCLUDED"


def test_financing_converts_documented_szse_100m_yuan_to_canonical_yuan():
    good = pd.DataFrame({
        "date": ["2026-09-15", "2026-09-16"],
        "sse_financing_balance": [9.0e11, 9.1e11],
        "szse_financing_balance": [8000.0, 8100.0],
        "sse_source_unit": ["yuan", "yuan"],
        "szse_source_unit": ["100_million_yuan", "100_million_yuan"],
    })
    canonical, state = qualify_financing_yuan(good)
    assert state["state"] == "CANONICAL_UNIT_QUALIFIED"
    assert state["sse_raw_unit"] == "CNY"
    assert state["szse_raw_unit"] == "CNY_100M"
    assert canonical.loc[0, "szse_financing_balance_yuan"] == 8.0e11
    assert canonical.loc[0, "financing_balance_yuan"] == 1.7e12
    assert canonical.loc[0, "canonical_unit"] == "CNY"

    wrong_units = good.copy()
    wrong_units["szse_source_unit"] = "yuan"
    canonical, state = qualify_financing_yuan(wrong_units)
    assert canonical.empty
    assert state["state"] == "QUARANTINED_UNIT_UNVERIFIED"


def test_turnover_fetcher_keeps_exchange_failures_explicit():
    dates = pd.to_datetime(["2026-09-15", "2026-09-16"])

    def sse_fetch(date):
        if date == "20260915":
            raise RuntimeError("provider down")
        return pd.DataFrame({
            "单日情况": ["成交金额"],
            "主板A": [1000.0],
            "科创板": [200.0],
        })

    def szse_fetch(date):
        return pd.DataFrame({
            "证券类别": ["股票", "主板B股"],
            "成交金额": [150_100_000_000.0, 100_000_000.0],
        })

    result = fetch_sse_szse_a_share_turnover_history(
        trading_dates=dates,
        sse_fetcher=sse_fetch,
        szse_fetcher=szse_fetch,
        sleep_seconds=0,
    )
    assert len(result.combined) == 1
    assert result.combined.loc[0, "szse_a_share_turnover_yuan"] == 150_000_000_000
    assert result.errors.to_dict("records")[0]["exchange"] == "SSE"


def test_turnover_fetcher_retries_transient_transport_failure():
    calls = 0

    def sse_fetch(date):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(104, "reset")
        return pd.DataFrame({
            "单日情况": ["成交金额"],
            "主板A": [1000.0],
            "科创板": [200.0],
        })

    def szse_fetch(date):
        return pd.DataFrame({
            "证券类别": ["股票", "主板B股"],
            "成交金额": [150_100_000_000.0, 100_000_000.0],
        })

    result = fetch_sse_szse_a_share_turnover_history(
        trading_dates=pd.to_datetime(["2026-09-16"]),
        sse_fetcher=sse_fetch,
        szse_fetcher=szse_fetch,
        sleep_seconds=0,
        retry_backoff_seconds=0,
    )
    assert calls == 2
    assert result.errors.empty
    assert len(result.combined) == 1


def test_sse_historical_turnover_payload_maps_main_a_and_star_trade_amount():
    payload = {
        "result": [
            {"PRODUCT_TYPE": "1", "TX_AMOUNT": "3713.66"},
            {"PRODUCT_TYPE": "2", "TX_AMOUNT": "1.36"},
            {"PRODUCT_TYPE": "43", "TX_AMOUNT": "378.23"},
            {"PRODUCT_TYPE": "40", "TX_AMOUNT": "4093.25"},
        ]
    }
    frame = _sse_historical_turnover_payload_frame(payload)
    assert frame.to_dict("records") == [
        {"单日情况": "成交金额", "主板A": 3713.66, "科创板": 378.23}
    ]
    normalized = normalize_sse_a_share_turnover(
        frame, observation_date="2021-12-23"
    )
    assert normalized["sse_a_share_turnover_yuan"] == (
        3713.66 + 378.23
    ) * 100_000_000.0


def test_sse_historical_turnover_fetch_uses_official_historical_sql_contract():
    seen = {}

    class FakeResponse:
        url = SSE_TURNOVER_QUERY_URL
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": [
                    {"PRODUCT_TYPE": "1", "TX_AMOUNT": "3000.0"},
                    {"PRODUCT_TYPE": "43", "TX_AMOUNT": "400.0"},
                ]
            }

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        seen["headers"] = kwargs["headers"]
        return FakeResponse()

    frame = _fetch_sse_historical_daily_overview(
        "20211223",
        plain_get=fake_get,
    )
    assert seen["url"] == SSE_TURNOVER_QUERY_URL
    assert seen["params"] == {
        "searchDate": "2021-12-23",
        "sqlId": SSE_TURNOVER_HISTORICAL_SQL_ID,
        "stockType": "90",
    }
    assert "index_his.shtml" in seen["headers"]["Referer"]
    assert frame.loc[0, "主板A"] == 3000.0
    assert frame.loc[0, "科创板"] == 400.0
    assert frame.attrs["historical_sql_id"] == SSE_TURNOVER_HISTORICAL_SQL_ID
