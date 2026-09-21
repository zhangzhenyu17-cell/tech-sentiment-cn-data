import json

import pandas as pd

from tech_sentiment.capital_input_data import EtfShareFetchResult, ExchangeTurnoverFetchResult
from tech_sentiment.financing_materialization import FinancingMaterializationResult
from tech_sentiment.resumable_capital import (
    materialize_capital_monthly,
    materialize_szse_etf_monthly,
)
from tech_sentiment.v4c03_szse_etf_shares import SzseEtfShareFetchResult
from tech_sentiment.resumable_financing import materialize_financing_monthly


def _etf_result(dates, fund_codes):
    dates = pd.DatetimeIndex(dates)
    code = list(fund_codes)[0]
    data = pd.DataFrame(
        {
            "date": dates,
            "fund_code": code,
            "fund_shares": [100.0 + i for i in range(len(dates))],
            "unit": "share",
            "source_identity": "SSE_ETF_SCALE_DAILY",
            "source_url": "https://www.sse.com.cn/assortment/fund/etf/list/scale/",
            "provider_interface": "fake",
            "evidence_available_date": dates,
        }
    )
    return EtfShareFetchResult(data=data, errors=pd.DataFrame(columns=["date", "error"]))


def _turnover_result(dates):
    dates = pd.DatetimeIndex(dates)
    sse = pd.DataFrame({"date": dates, "sse_a_share_turnover_yuan": 100.0})
    szse = pd.DataFrame({"date": dates, "szse_a_share_turnover_yuan": 200.0})
    combined = pd.DataFrame(
        {
            "date": dates,
            "sse_a_share_turnover_yuan": 100.0,
            "szse_a_share_turnover_yuan": 200.0,
            "amount": 300.0,
            "scope": "SSE_SZSE_A_SHARES",
            "canonical_all_a_state": "INCOMPLETE_BSE_NOT_INCLUDED",
        }
    )
    return ExchangeTurnoverFetchResult(
        sse=sse,
        szse=szse,
        combined=combined,
        errors=pd.DataFrame(columns=["date", "exchange", "error"]),
    )


def test_capital_fresh_equals_resumed(tmp_path):
    calls = {"etf": 0, "turnover": 0}

    def etf_fetcher(*, trading_dates, fund_codes, sleep_seconds):
        calls["etf"] += 1
        return _etf_result(trading_dates, fund_codes)

    def turnover_fetcher(*, trading_dates, sleep_seconds):
        calls["turnover"] += 1
        return _turnover_result(trading_dates)

    dates = pd.to_datetime(["2026-01-30", "2026-02-02", "2026-02-03"])
    first = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )
    assert first.executed_chunks == 4
    assert calls == {"etf": 2, "turnover": 2}

    second = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )
    assert second.resumed_chunks == 4
    assert calls == {"etf": 2, "turnover": 2}
    pd.testing.assert_frame_equal(first.etf.data, second.etf.data, check_dtype=False)
    pd.testing.assert_frame_equal(
        first.turnover.combined, second.turnover.combined, check_dtype=False
    )



def test_capital_semantic_revision_reuses_across_operational_commits_but_not_dates(
    tmp_path,
):
    calls = {"etf": 0, "turnover": 0}

    def etf_fetcher(*, trading_dates, fund_codes, sleep_seconds):
        calls["etf"] += 1
        return _etf_result(trading_dates, fund_codes)

    def turnover_fetcher(*, trading_dates, sleep_seconds):
        calls["turnover"] += 1
        return _turnover_result(trading_dates)

    dates = pd.to_datetime(["2026-09-18", "2026-09-21"])
    first = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="commit-a",
        checkpoint_revision="semantic:stable",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )
    assert first.executed_chunks == 2

    second = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="commit-b",
        checkpoint_revision="semantic:stable",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )
    assert second.resumed_chunks == 2
    assert calls == {"etf": 1, "turnover": 1}

    third = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="commit-c",
        checkpoint_revision="semantic:stable",
        capture_date="2026-09-22",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )
    assert third.executed_chunks == 2
    assert calls == {"etf": 2, "turnover": 2}


def test_szse_etf_monthly_fresh_equals_resumed_across_operational_commit(tmp_path):
    calls = {"n": 0}

    def fetcher(
        *,
        start_date,
        end_date,
        trading_dates,
        fund_codes,
        sleep_seconds,
    ):
        calls["n"] += 1
        dates = pd.DatetimeIndex(trading_dates)
        code = list(fund_codes)[0]
        return SzseEtfShareFetchResult(
            data=pd.DataFrame(
                {
                    "date": dates,
                    "fund_code": code,
                    "fund_shares": [200.0 + i for i in range(len(dates))],
                    "unit": "share",
                    "source_identity": "SZSE_ETF_SCALE_DAILY",
                    "provider": "fake",
                    "evidence_available_date": dates,
                }
            ),
            errors=pd.DataFrame(
                columns=["chunk_start", "chunk_end", "error"]
            ),
        )

    dates = pd.to_datetime(["2026-08-31", "2026-09-01", "2026-09-21"])
    first = materialize_szse_etf_monthly(
        trading_dates=dates,
        fund_codes=["159915"],
        source_commit="commit-a",
        checkpoint_revision="semantic:szse",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        fetcher=fetcher,
    )
    assert first.executed_chunks == 2
    assert calls["n"] == 2

    second = materialize_szse_etf_monthly(
        trading_dates=dates,
        fund_codes=["159915"],
        source_commit="commit-b",
        checkpoint_revision="semantic:szse",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        fetcher=fetcher,
    )
    assert second.resumed_chunks == 2
    assert calls["n"] == 2
    pd.testing.assert_frame_equal(
        first.result.data,
        second.result.data,
        check_dtype=False,
    )


def test_sse_etf_missing_operation_row_is_never_permanent_reuse_eligible(tmp_path):
    def etf_fetcher(*, trading_dates, fund_codes, sleep_seconds):
        dates = pd.DatetimeIndex(trading_dates)
        code = list(fund_codes)[0]
        observed = dates[:-1]
        return EtfShareFetchResult(
            data=pd.DataFrame(
                {
                    "date": observed,
                    "fund_code": code,
                    "fund_shares": [100.0 + i for i in range(len(observed))],
                }
            ),
            errors=pd.DataFrame(
                {
                    "date": [dates[-1]],
                    "error": ["NO_MATCHING_ETF_ROW"],
                }
            ),
        )

    def turnover_fetcher(*, trading_dates, sleep_seconds):
        return _turnover_result(trading_dates)

    dates = pd.to_datetime(["2026-09-18", "2026-09-21"])
    materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="commit-a",
        checkpoint_revision="semantic:stable",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )

    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.glob("*/receipt.json")
    ]
    etf_receipts = [
        item
        for item in receipts
        if item["identity"]["producer"] == "sse-etf-share-history"
    ]
    assert len(etf_receipts) == 1
    assert etf_receipts[0]["metadata"]["permanent_reuse_eligible"] is False


def test_turnover_missing_required_date_is_never_permanent_reuse_eligible(tmp_path):
    def etf_fetcher(*, trading_dates, fund_codes, sleep_seconds):
        return _etf_result(trading_dates, fund_codes)

    def turnover_fetcher(*, trading_dates, sleep_seconds):
        dates = pd.DatetimeIndex(trading_dates)
        return _turnover_result(dates[:-1])

    dates = pd.to_datetime(["2026-09-18", "2026-09-21"])
    materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=["588000"],
        source_commit="commit-a",
        checkpoint_revision="semantic:stable",
        capture_date="2026-09-21",
        checkpoint_dir=tmp_path,
        sleep_seconds=0,
        etf_history_fetcher=etf_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )

    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.glob("*/receipt.json")
    ]
    turnover_receipts = [
        item
        for item in receipts
        if item["identity"]["producer"] == "sse-szse-a-share-turnover-history"
    ]
    assert len(turnover_receipts) == 1
    assert turnover_receipts[0]["metadata"]["permanent_reuse_eligible"] is False

def _financing_chunk(dates):
    dates = pd.DatetimeIndex(dates)
    raw = pd.DataFrame(
        {
            "date": dates,
            "sse_financing_balance": 1.0e12,
            "sse_source_unit": "CNY",
            "sse_source_identity": "SSE_MARGIN_SUMMARY",
            "sse_source_url": "https://www.sse.com.cn/market/othersdata/margin/sum/",
            "szse_financing_balance": 5000.0,
            "szse_source_unit": "CNY_100M",
            "szse_source_identity": "SZSE_MARGIN_SUMMARY",
            "szse_source_url": "https://www.szse.cn/disclosure/margin/margin/index.html",
            "bilateral_complete": True,
        }
    )
    canonical = pd.DataFrame()
    return FinancingMaterializationResult(
        raw=raw,
        canonical=canonical,
        errors=pd.DataFrame(columns=["date", "exchange", "error"]),
        summary={"qualification_state": "CANONICAL_UNIT_QUALIFIED"},
    )


def test_financing_fresh_equals_resumed(tmp_path):
    calls = {"n": 0}

    def materialize_one(dates):
        calls["n"] += 1
        return _financing_chunk(dates)

    dates = pd.to_datetime(["2026-01-30", "2026-02-02", "2026-02-03"])
    first = materialize_financing_monthly(
        trading_dates=dates,
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        materialize_one=materialize_one,
    )
    assert first.executed_chunks == 2
    assert calls["n"] == 2
    assert first.result.summary["bilateral_coverage"] == 1.0
    assert first.result.summary["qualification_state"] == "CANONICAL_UNIT_QUALIFIED"

    second = materialize_financing_monthly(
        trading_dates=dates,
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        materialize_one=materialize_one,
    )
    assert second.resumed_chunks == 2
    assert calls["n"] == 2
    pd.testing.assert_frame_equal(first.result.raw, second.result.raw, check_dtype=False)
    pd.testing.assert_frame_equal(
        first.result.canonical, second.result.canonical, check_dtype=False
    )
