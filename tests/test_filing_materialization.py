from __future__ import annotations

import pandas as pd
import pytest

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


def _announcement(title: str, when: str, announcement_id: str) -> dict[str, str]:
    return {
        "代码": "688012",
        "简称": "中微公司",
        "公告标题": title,
        "公告时间": when,
        "公告链接": (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"stockCode=688012&announcementId={announcement_id}&orgId=gssh0600688"
        ),
        "公告附件链接": f"https://static.cninfo.com.cn/finalpage/2020-04-29/{announcement_id}.PDF",
    }


def test_same_time_body_and_complete_report_prefers_complete_carrier():
    raw = pd.DataFrame(
        [
            _announcement("2020年第一季度报告正文", "2020-04-29 00:00:00", "1207671800"),
            _announcement("2020年第一季度报告", "2020-04-29 00:00:00", "1207671801"),
        ]
    )
    selected = filing_materialization._select_primary_numeric_filing_candidates(raw)

    assert list(selected["公告标题"]) == ["2020年第一季度报告"]
    assert "1207671801" in selected.iloc[0]["公告链接"]


def test_same_time_body_and_full_report_prefers_full_carrier():
    raw = pd.DataFrame(
        [
            _announcement("2020年第一季度报告正文", "2020-04-29 00:00:00", "body"),
            _announcement("2020年第一季度报告全文", "2020-04-29 00:00:00", "full"),
        ]
    )
    selected = filing_materialization._select_primary_numeric_filing_candidates(raw)

    assert list(selected["公告标题"]) == ["2020年第一季度报告全文"]


def test_body_report_is_retained_when_no_complete_carrier_exists():
    raw = pd.DataFrame(
        [
            _announcement("2020年第一季度报告正文", "2020-04-29 00:00:00", "body"),
        ]
    )
    selected = filing_materialization._select_primary_numeric_filing_candidates(raw)

    assert list(selected["公告标题"]) == ["2020年第一季度报告正文"]


def test_revision_identity_is_not_collapsed_with_original_title_family():
    raw = pd.DataFrame(
        [
            _announcement("2020年第一季度报告", "2020-04-29 00:00:00", "original"),
            _announcement("2020年第一季度报告（修订版）", "2020-04-29 00:00:00", "revision"),
        ]
    )
    selected = filing_materialization._select_primary_numeric_filing_candidates(raw)

    assert set(selected["公告标题"]) == {
        "2020年第一季度报告",
        "2020年第一季度报告（修订版）",
    }


def test_conflicting_cached_documents_recheck_exact_official_pdf_carriers(monkeypatch):
    facts = pd.DataFrame(
        [
            {
                "entity_id": "688002.SH",
                "period_end": "2020-12-31",
                "fact_type": "OPERATING_CASH_FLOW_NET",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2021-04-28",
                "publication_timestamp": "2021-04-28 00:00:00",
                "document_id": "1209844800",
                "document_url": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209844800.PDF",
                "document_sha256": "a" * 64,
                "document_presentation_variant": "",
            },
            {
                "entity_id": "688002.SH",
                "period_end": "2020-12-31",
                "fact_type": "OPERATING_CASH_FLOW_NET",
                "value": 120.0,
                "unit": "CNY",
                "evidence_available_date": "2021-04-28",
                "publication_timestamp": "2021-04-28 00:00:00",
                "document_id": "1209844801",
                "document_url": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209844801.PDF",
                "document_sha256": "b" * 64,
                "document_presentation_variant": "",
            },
        ]
    )

    class Downloaded:
        def __init__(self, url, sha256, content):
            self.url = url
            self.sha256 = sha256
            self.content = content

    def fake_download(url):
        if url.endswith("1209844800.PDF"):
            return Downloaded(url, "a" * 64, b"summary")
        return Downloaded(url, "b" * 64, b"full")

    def fake_extract(content):
        if content == b"summary":
            return "烟台睿创微纳技术股份有限公司 2020 年年度报告摘要"
        return "烟台睿创微纳技术股份有限公司 2020 年年度报告"

    monkeypatch.setattr(filing_materialization, "download_official_document", fake_download)
    monkeypatch.setattr(filing_materialization, "extract_pdf_text", fake_extract)

    enriched, rechecked = filing_materialization._enrich_conflicting_presentation_variants(
        facts
    )
    variants = dict(
        zip(
            enriched["document_id"].astype(str),
            enriched["document_presentation_variant"].astype(str),
        )
    )
    assert rechecked == 2
    assert variants["1209844800"] == "SUMMARY"
    assert variants["1209844801"] == "FULL_OR_CANONICAL"


def test_conflict_recheck_rejects_official_pdf_sha_drift(monkeypatch):
    facts = pd.DataFrame(
        [
            {
                "entity_id": "688002.SH",
                "period_end": "2020-12-31",
                "fact_type": "OPERATING_CASH_FLOW_NET",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2021-04-28",
                "publication_timestamp": "2021-04-28 00:00:00",
                "document_id": "one",
                "document_url": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209844800.PDF",
                "document_sha256": "a" * 64,
                "document_presentation_variant": "",
            },
            {
                "entity_id": "688002.SH",
                "period_end": "2020-12-31",
                "fact_type": "OPERATING_CASH_FLOW_NET",
                "value": 120.0,
                "unit": "CNY",
                "evidence_available_date": "2021-04-28",
                "publication_timestamp": "2021-04-28 00:00:00",
                "document_id": "two",
                "document_url": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209844801.PDF",
                "document_sha256": "b" * 64,
                "document_presentation_variant": "",
            },
        ]
    )

    class Downloaded:
        url = "https://static.cninfo.com.cn/finalpage/2021-04-28/1209844800.PDF"
        sha256 = "c" * 64
        content = b"changed"

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: Downloaded(),
    )

    with pytest.raises(ValueError, match="official filing bytes changed"):
        filing_materialization._enrich_conflicting_presentation_variants(facts)
