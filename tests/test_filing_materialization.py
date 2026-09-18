from __future__ import annotations

import pandas as pd

import tech_sentiment.filing_materialization as filing_materialization
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.official_filing_facts import (
    DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS,
    FILING_FACT_COLUMNS,
    derive_fundamental_trend_evidence,
)


def test_all_failed_filing_queries_return_schemaful_data_insufficient(monkeypatch, tmp_path):
    def fail_query(**kwargs):
        raise RuntimeError("fixture CNINFO unavailable")

    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        fail_query,
    )
    result = filing_materialization.materialize_versioned_filing_facts(
        ["600519"],
        target_start_date="2022-01-04",
        end_date="2022-01-07",
        trading_dates=pd.date_range("2022-01-04", "2022-01-07", freq="B"),
        source_commit="a" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
        warmup_years=2,
    )
    assert result.facts.empty
    assert list(result.facts.columns) == list(FILING_FACT_COLUMNS)
    assert result.trends.empty
    assert list(result.trends.columns) == list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS)
    assert result.summary["numerical_trend_materialization_state"] == "DATA_INSUFFICIENT"
    assert result.coverage.iloc[0]["query_status"] == "FAILED"

    fundamental = materialize_fundamental_state_evidence(
        result.facts,
        target_start_date="2022-01-04",
        target_end_date="2022-01-07",
    )
    assert fundamental.evidence.empty
    assert fundamental.coverage.empty
    assert fundamental.summary["readiness_state"] == "DATA_INSUFFICIENT"


def test_empty_fact_schema_is_consumable_by_trend_derivation():
    empty = pd.DataFrame(columns=list(FILING_FACT_COLUMNS))
    trends = derive_fundamental_trend_evidence(empty)
    assert trends.empty
    assert list(trends.columns) == list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS)



def test_numeric_financial_filing_title_excludes_non_primary_report_variants():
    predicate = filing_materialization.is_numeric_financial_filing_title

    assert predicate("贵州茅台2022年年度报告") is True
    assert predicate("贵州茅台2022年年度报告（修订版）") is True
    assert predicate("贵州茅台2022年年度报告（英文版）") is False
    assert predicate("贵州茅台2022年年度报告摘要") is False
    assert predicate("关于贵州茅台2022年年度报告的问询函回复") is False
    assert predicate("贵州茅台2022年度审计报告") is False
