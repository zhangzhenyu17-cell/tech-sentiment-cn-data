import pandas as pd

from tech_sentiment.capital_input_data import (
    combine_sse_szse_a_share_turnover,
    normalize_sse_a_share_turnover,
    normalize_sse_etf_share_snapshot,
    normalize_szse_a_share_turnover,
    qualify_financing_yuan,
    fetch_sse_szse_a_share_turnover_history,
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
    coverage = qualify_trailing_etf_coverage(history, trading_dates=cal, fund_code="588000")
    assert coverage.iloc[-1]["coverage"] == 0.8
    assert bool(coverage.iloc[-1]["eligible"])
    assert int(coverage["observed"].sum()) == 48


def test_sse_szse_turnover_normalization_is_explicit_about_scope():
    sse_raw = pd.DataFrame({
        "单日情况": ["成交金额"],
        "主板A": [3713.66],
        "主板B": [1.36],
        "科创板": [378.23],
    })
    szse_raw = pd.DataFrame({
        "证券类别": ["主板A股", "主板B股", "创业板A股", "基金"],
        "成交金额": [300_000_000_000.0, 100_000_000.0, 200_000_000_000.0, 50_000_000_000.0],
    })
    sse = pd.DataFrame([normalize_sse_a_share_turnover(sse_raw, observation_date="2026-09-16")])
    szse = pd.DataFrame([normalize_szse_a_share_turnover(szse_raw, observation_date="2026-09-16")])
    combined = combine_sse_szse_a_share_turnover(sse, szse)
    expected_sse = (3713.66 + 378.23) * 100_000_000
    assert combined.loc[0, "sse_a_share_turnover_yuan"] == expected_sse
    assert combined.loc[0, "szse_a_share_turnover_yuan"] == 500_000_000_000
    assert combined.loc[0, "scope"] == "SSE_SZSE_A_SHARES"
    assert combined.loc[0, "canonical_all_a_state"] == "INCOMPLETE_BSE_NOT_INCLUDED"


def test_financing_only_qualifies_documented_yuan_scale():
    good = pd.DataFrame({
        "date": ["2026-09-15", "2026-09-16"],
        "sse_financing_balance": [9.0e11, 9.1e11],
        "szse_financing_balance": [8.0e11, 8.1e11],
        "sse_source_unit": ["yuan", "yuan"],
        "szse_source_unit": ["yuan", "yuan"],
    })
    canonical, state = qualify_financing_yuan(good)
    assert state["state"] == "CANONICAL_UNIT_QUALIFIED"
    assert canonical.loc[0, "financing_balance_yuan"] == 1.7e12

    bad = good.copy()
    bad["szse_financing_balance"] = [8000.0, 8100.0]
    canonical, state = qualify_financing_yuan(bad)
    assert canonical.empty
    assert state["state"] == "QUARANTINED_UNIT_MISMATCH"


def test_turnover_fetcher_keeps_exchange_failures_explicit():
    dates = pd.to_datetime(["2026-09-15", "2026-09-16"])
    def sse_fetch(date):
        if date == "20260915":
            raise RuntimeError("provider down")
        return pd.DataFrame({"单日情况": ["成交金额"], "主板A": [1000.0], "科创板": [200.0]})
    def szse_fetch(date):
        return pd.DataFrame({"证券类别": ["主板A股", "创业板A股"], "成交金额": [1e11, 5e10]})
    result = fetch_sse_szse_a_share_turnover_history(
        trading_dates=dates, sse_fetcher=sse_fetch, szse_fetcher=szse_fetch, sleep_seconds=0
    )
    assert len(result.combined) == 1
    assert result.errors.to_dict("records")[0]["exchange"] == "SSE"
