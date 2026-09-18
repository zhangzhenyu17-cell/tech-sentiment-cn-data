import json

import pandas as pd
import pytest

from tech_sentiment.official_filing_facts import (
    build_filing_fact_rows,
    derive_fundamental_trend_evidence,
    extract_standard_filing_facts,
    filing_period_end_from_title,
)


def test_filing_period_end_is_derived_from_the_versioned_report_title():
    assert filing_period_end_from_title("某公司2025年年度报告") == pd.Timestamp("2025-12-31")
    assert filing_period_end_from_title("某公司2026年第一季度报告") == pd.Timestamp("2026-03-31")
    assert filing_period_end_from_title("某公司2026年半年度报告（修订版）") == pd.Timestamp("2026-06-30")
    assert filing_period_end_from_title("某公司2026年第三季度报告") == pd.Timestamp("2026-09-30")


def test_standard_filing_facts_require_proven_yuan_units_and_do_not_fill():
    text = """
    主要会计数据和财务指标  单位：元 币种：人民币
    营业收入 1,200.00 1,000.00
    归属于上市公司股东的净利润 120.00 100.00
    经营活动产生的现金流量净额 90.00 80.00
    总资产 5,000.00 4,800.00
    归属于上市公司股东的所有者权益 3,000.00 2,900.00
    基本每股收益 1.20 1.00
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 1200.0
    assert facts["NET_PROFIT_PARENT"] == 120.0
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(0.1)
    assert facts["BASIC_EPS"] == 1.2
    assert "NONEXISTENT" not in facts

    with pytest.raises(ValueError, match="non-yuan unit"):
        extract_standard_filing_facts("单位：万元\n营业收入 10 9")


def _facts(
    title: str,
    available: str,
    doc: str,
    revenue: float,
    profit: float,
    published: str | None = None,
) -> pd.DataFrame:
    text = f"""
    主要会计数据和财务指标 单位：元 币种：人民币
    营业收入 {revenue} {revenue - 1}
    归属于上市公司股东的净利润 {profit} {profit - 1}
    """
    return build_filing_fact_rows(
        entity_id="600000.SH",
        title=title,
        evidence_available_date=available,
        publication_timestamp=published or available,
        source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
        provider="CNINFO",
        document_id=doc,
        revision_id=f"DOCUMENT:{doc}",
        document_url=f"https://static.cninfo.com.cn/finalpage/{doc}.PDF",
        document_sha256=(doc * 64)[:64],
        text=text,
    )


def test_derived_trends_use_only_prior_version_available_as_of_current_filing():
    old_2024 = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    current_2025 = _facts("2025年年度报告", "2026-04-20", "b", 120.0, 12.0)
    future_restatement_2024 = _facts("2024年年度报告（修订版）", "2026-06-01", "c", 200.0, 20.0)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([old_2024, current_2025, future_restatement_2024], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a"
    assert payload["yoy_change"] == pytest.approx(0.2)
    assert pd.Timestamp(revenue["evidence_available_date"]) == pd.Timestamp("2026-04-20")


def test_later_restatement_is_append_only_not_history_rewrite():
    original = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    restated = _facts("2024年年度报告（修订版）", "2025-06-01", "r", 110.0, 11.0)
    current = _facts("2025年年度报告", "2026-04-20", "b", 121.0, 12.1)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, restated, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "r"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_close_date_revision_uses_official_publication_order_not_document_id():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 09:00:00",
    )
    revision = _facts(
        "2024年年度报告（修订版）",
        "2025-04-21",
        "a_revision",
        110.0,
        11.0,
        published="2025-04-21 14:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_revision"
    assert payload["yoy_change"] == pytest.approx(0.1)
