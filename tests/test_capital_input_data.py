import pandas as pd
import pytest

import tech_sentiment.capital_input_data as capital_module
from tech_sentiment.capital_input_data import (
    SSE_ETF_SCALE_QUERY_URL,
    SSE_ETF_SCALE_SOA_QUERY_URL,
    SSE_ETF_SHARE_SOURCE_URL,
    _fetch_sse_etf_scale_direct,
    _sse_etf_exact_payload_frame,
    _sse_etf_scale_payload_frame,
    _sse_turnover_payload_frame,
    _szse_turnover_payload_frame,
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



def test_exact_etf_fallback_requires_exact_code_date_and_preserves_units():
    frame = _sse_etf_exact_payload_frame(
        {"result": [{"SEC_CODE": "588000", "STAT_DATE": "2026-09-17", "TOT_VOL": "900001.23"}]},
        date="20260917",
        fund_code="588000",
    )
    assert frame.loc[0, "基金份额"] == 9_000_012_300.0
    with pytest.raises(ValueError, match="exactly one target code/date"):
        _sse_etf_exact_payload_frame(
            {"result": [{"SEC_CODE": "510300", "STAT_DATE": "2026-09-17", "TOT_VOL": "1"}]},
            date="20260917",
            fund_code="588000",
        )


def test_etf_history_uses_exact_query_only_after_interface_family_exhaustion(monkeypatch):
    monkeypatch.setattr(
        capital_module,
        "_fetch_sse_etf_scale_direct",
        lambda date: (_ for _ in ()).throw(RuntimeError("bulk blocked")),
    )
    monkeypatch.setattr(
        capital_module,
        "_fetch_sse_etf_scale_exact",
        lambda date, code: pd.DataFrame(
            {
                "基金代码": [code],
                "统计日期": [pd.Timestamp("2026-09-17").date()],
                "基金份额": [9_000_012_300.0],
            }
        ),
    )
    result = capital_module.fetch_sse_etf_share_history(
        trading_dates=["2026-09-17"],
        fund_codes=(code for code in ["588000"]),
        sleep_seconds=0,
    )
    assert result.errors.empty
    assert result.data.loc[0, "fund_shares"] == 9_000_012_300.0


def test_official_turnover_payloads_preserve_canonical_yuan_contract():
    sse_rows = [
        {f"k{i}": base + i for i in range(11)}
        for base in (1000.0, 1.0, 200.0)
    ]
    sse_frame = _sse_turnover_payload_frame({"result": sse_rows})
    sse = normalize_sse_a_share_turnover(sse_frame, observation_date="2026-09-17")
    assert sse["sse_a_share_turnover_yuan"] == pytest.approx(
        (1004.0 + 204.0) * 100_000_000.0
    )

    szse_frame = _szse_turnover_payload_frame(
        [{"data": [{"zqlb": "股票", "cjje": "5,001.00"}, {"zqlb": "主板B股", "cjje": "1.00"}]}]
    )
    szse = normalize_szse_a_share_turnover(szse_frame, observation_date="2026-09-17")
    assert szse["szse_a_share_turnover_yuan"] == pytest.approx(
        5000.0 * 100_000_000.0
    )

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
