from __future__ import annotations

import pandas as pd
import pytest

import tech_sentiment.filing_materialization as filing_materialization
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.immutable_checkpoint import ImmutableCheckpointStore
from tech_sentiment.official_filing_facts import (
    DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS,
    FILING_FACT_COLUMNS,
    FILING_PARSER_VERSION,
    LEGACY_FILING_PARSER_VERSION,
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


def test_incomplete_legacy_facts_require_parser_upgrade_but_complete_core_facts_do_not():
    partial = pd.DataFrame({"fact_type": ["OPERATING_REVENUE", "BASIC_EPS"]})
    complete = pd.DataFrame(
        {
            "fact_type": [
                "OPERATING_REVENUE",
                "NET_PROFIT_PARENT",
                "OPERATING_CASH_FLOW_NET",
                "NET_PROFIT_MARGIN",
            ]
        }
    )
    assert filing_materialization._facts_require_parser_upgrade(partial) is True
    assert filing_materialization._facts_require_parser_upgrade(complete) is False


def test_legacy_incomplete_checkpoint_is_sha_verified_and_selectively_reparsed(
    monkeypatch,
    tmp_path,
):
    legacy_commit = "1" * 40
    current_commit = "2" * 40
    document_id = "1200000001"
    attachment = (
        "https://static.cninfo.com.cn/finalpage/2025-04-30/1200000001.PDF"
    )
    raw = pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2024年年度报告",
                "公告时间": "2025-04-30 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": attachment,
            }
        ]
    )
    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: raw.copy(),
    )

    legacy_facts = pd.DataFrame(
        [
            {
                "entity_id": "688012.SH",
                "period_end": "2024-12-31",
                "fact_type": "OPERATING_REVENUE",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2025-04-30",
                "publication_timestamp": "2025-04-30 10:00:00",
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}:SHA256:{'a' * 64}",
                "document_url": attachment,
                "document_sha256": "a" * 64,
                "parser_version": LEGACY_FILING_PARSER_VERSION,
            }
        ],
        columns=list(FILING_FACT_COLUMNS),
    )
    store = ImmutableCheckpointStore(tmp_path / "checkpoint")
    legacy_identity = filing_materialization._document_identity(
        source_commit=legacy_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=LEGACY_FILING_PARSER_VERSION,
    )
    store.save(
        legacy_identity,
        frames={"facts": legacy_facts},
        metadata={
            "document_sha256": "a" * 64,
            "document_presentation_variant": "FULL_OR_CANONICAL",
        },
    )

    class Downloaded:
        url = attachment
        retrieval_url = attachment
        sha256 = "a" * 64
        content = b"reparse"

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: Downloaded(),
    )
    monkeypatch.setattr(
        filing_materialization,
        "extract_pdf_text",
        lambda content: """
            2024年年度报告
            主要会计数据 单位：人民币千元 币种：人民币
            营业收入 120 100
            归属于母公司所有者的净利润 12 10
            经营活动产生的现金流量净额 20 18
        """,
    )

    result = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit=current_commit,
        checkpoint_source_commit=legacy_commit,
        checkpoint_dir=tmp_path / "checkpoint",
        warmup_years=2,
    )

    assert result.summary["parser_upgrade_documents"] == 1
    assert result.summary["reused_legacy_complete_documents"] == 0
    assert result.summary["active_parser_version"] == FILING_PARSER_VERSION
    extracted = {
        row.fact_type: row.value
        for row in result.facts.itertuples()
        if row.document_id == document_id
    }
    assert extracted["OPERATING_REVENUE"] == pytest.approx(120_000.0)
    assert extracted["NET_PROFIT_PARENT"] == pytest.approx(12_000.0)
    assert extracted["OPERATING_CASH_FLOW_NET"] == pytest.approx(20_000.0)
    assert extracted["NET_PROFIT_MARGIN"] == pytest.approx(0.1)

    current_identity = filing_materialization._document_identity(
        source_commit=current_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=FILING_PARSER_VERSION,
    )
    upgraded = store.load(current_identity)
    assert upgraded is not None
    assert upgraded.receipt["metadata"]["parser_upgrade_from_legacy"] is True



def test_split_progress_store_reuses_legacy_read_only_and_survives_cross_commit_run(
    monkeypatch,
    tmp_path,
):
    legacy_commit = "1" * 40
    progress_commit = "2" * 40
    runtime_commit = "3" * 40
    document_id = "1200000099"
    attachment = (
        "https://static.cninfo.com.cn/finalpage/2025-04-30/1200000099.PDF"
    )
    raw = pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2024年年度报告",
                "公告时间": "2025-04-30 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": attachment,
            }
        ]
    )

    legacy_root = tmp_path / "legacy"
    progress_root = tmp_path / "progress"
    legacy_store = ImmutableCheckpointStore(legacy_root)
    progress_store = ImmutableCheckpointStore(progress_root)

    legacy_query = filing_materialization._symbol_query_identity(
        source_commit=legacy_commit,
        symbol="688012",
        query_start="2023-01-01",
        query_end="2025-05-02",
    )
    legacy_store.save(
        legacy_query,
        frames={"announcements": raw},
        metadata={"entity_id": "688012.SH"},
    )

    legacy_facts = pd.DataFrame(
        [
            {
                "entity_id": "688012.SH",
                "period_end": "2024-12-31",
                "fact_type": "OPERATING_REVENUE",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2025-04-30",
                "publication_timestamp": "2025-04-30 10:00:00",
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}:SHA256:{'a' * 64}",
                "document_url": attachment,
                "document_sha256": "a" * 64,
                "parser_version": LEGACY_FILING_PARSER_VERSION,
            }
        ],
        columns=list(FILING_FACT_COLUMNS),
    )
    legacy_identity = filing_materialization._document_identity(
        source_commit=legacy_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=LEGACY_FILING_PARSER_VERSION,
    )
    legacy_store.save(legacy_identity, frames={"facts": legacy_facts})

    class Downloaded:
        url = attachment
        retrieval_url = attachment
        sha256 = "a" * 64
        content = b"same-official-pdf"

    downloads = {"count": 0}

    def download(url):
        downloads["count"] += 1
        return Downloaded()

    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy query checkpoint should be reused")
        ),
    )
    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        download,
    )
    monkeypatch.setattr(
        filing_materialization,
        "extract_pdf_text",
        lambda content: """
            2024年年度报告
            主要会计数据 单位：人民币元 币种：人民币
            营业收入 120 100
            归属于母公司所有者的净利润 12 10
            经营活动产生的现金流量净额 20 18
        """,
    )

    first = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit=runtime_commit,
        checkpoint_source_commit=legacy_commit,
        progress_checkpoint_source_commit=progress_commit,
        checkpoint_dir=progress_root,
        legacy_checkpoint_dir=legacy_root,
        warmup_years=2,
    )
    assert downloads["count"] == 1
    assert first.summary["checkpoint_store_mode"] == (
        "SPLIT_LEGACY_AND_CURRENT_PROGRESS_STORES_V1"
    )
    assert first.summary["progress_checkpoint_source_commit"] == progress_commit
    assert first.summary["reused_legacy_symbol_queries"] == 1
    assert first.summary["parser_upgrade_documents"] == 1

    progress_identity = filing_materialization._document_identity(
        source_commit=progress_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=FILING_PARSER_VERSION,
    )
    assert progress_store.load(progress_identity) is not None
    assert legacy_store.load(progress_identity) is None

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: (_ for _ in ()).throw(
            AssertionError("durable progress checkpoint should avoid re-download")
        ),
    )
    second = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit="4" * 40,
        checkpoint_source_commit=legacy_commit,
        progress_checkpoint_source_commit=progress_commit,
        checkpoint_dir=progress_root,
        legacy_checkpoint_dir=legacy_root,
        warmup_years=2,
    )
    assert second.summary["resumed_current_parser_documents"] == 1
    assert second.summary["parser_upgrade_documents"] == 0
    assert second.summary["progress_checkpoint_source_commit"] == progress_commit


def test_legacy_parser_upgrade_rejects_sha_drift(monkeypatch, tmp_path):
    legacy_commit = "1" * 40
    current_commit = "2" * 40
    document_id = "1200000002"
    attachment = (
        "https://static.cninfo.com.cn/finalpage/2025-04-30/1200000002.PDF"
    )
    raw = pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2024年年度报告",
                "公告时间": "2025-04-30 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": attachment,
            }
        ]
    )
    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: raw.copy(),
    )
    legacy_facts = pd.DataFrame(
        [
            {
                "entity_id": "688012.SH",
                "period_end": "2024-12-31",
                "fact_type": "OPERATING_REVENUE",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2025-04-30",
                "publication_timestamp": "2025-04-30 10:00:00",
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}:SHA256:{'a' * 64}",
                "document_url": attachment,
                "document_sha256": "a" * 64,
                "parser_version": LEGACY_FILING_PARSER_VERSION,
            }
        ],
        columns=list(FILING_FACT_COLUMNS),
    )
    store = ImmutableCheckpointStore(tmp_path / "checkpoint")
    legacy_identity = filing_materialization._document_identity(
        source_commit=legacy_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=LEGACY_FILING_PARSER_VERSION,
    )
    store.save(legacy_identity, frames={"facts": legacy_facts})

    class Downloaded:
        url = attachment
        retrieval_url = attachment
        sha256 = "b" * 64
        content = b"changed"

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: Downloaded(),
    )

    result = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit=current_commit,
        checkpoint_source_commit=legacy_commit,
        checkpoint_dir=tmp_path / "checkpoint",
        warmup_years=2,
    )
    assert result.coverage.iloc[0]["query_status"] == "FAILED"
    assert "official filing bytes changed during parser upgrade" in result.errors.iloc[0]["error"]


def test_noninferable_filing_content_is_soft_data_insufficiency(monkeypatch, tmp_path):
    document_id = "1200000003"
    attachment = (
        "https://static.cninfo.com.cn/finalpage/2025-04-30/1200000003.PDF"
    )
    raw = pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2024年年度报告",
                "公告时间": "2025-04-30 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": attachment,
            }
        ]
    )
    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: raw.copy(),
    )

    class Downloaded:
        url = attachment
        retrieval_url = attachment
        sha256 = "c" * 64
        content = b"image-only"

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: Downloaded(),
    )

    def no_text(content):
        raise ValueError("official filing has no extractable text layer")

    monkeypatch.setattr(filing_materialization, "extract_pdf_text", no_text)

    result = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit="3" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
        warmup_years=2,
    )

    assert (
        result.coverage.iloc[0]["query_status"]
        == "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
    )
    assert result.errors.iloc[0]["severity"] == "SOFT_DATA_INSUFFICIENCY"
    assert result.summary["soft_data_insufficient_entities"] == 1


def test_filing_document_sha_drift_remains_hard_failure(monkeypatch, tmp_path):
    # Reuse the dedicated legacy-upgrade SHA drift fixture semantics and assert
    # the new hard/soft split does not downgrade integrity failures.
    legacy_commit = "1" * 40
    current_commit = "4" * 40
    document_id = "1200000004"
    attachment = (
        "https://static.cninfo.com.cn/finalpage/2025-04-30/1200000004.PDF"
    )
    raw = pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2024年年度报告",
                "公告时间": "2025-04-30 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": attachment,
            }
        ]
    )
    monkeypatch.setattr(
        filing_materialization,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: raw.copy(),
    )
    legacy_facts = pd.DataFrame(
        [
            {
                "entity_id": "688012.SH",
                "period_end": "2024-12-31",
                "fact_type": "OPERATING_REVENUE",
                "value": 100.0,
                "unit": "CNY",
                "evidence_available_date": "2025-04-30",
                "publication_timestamp": "2025-04-30 10:00:00",
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "provider": "CNINFO",
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}:SHA256:{'a' * 64}",
                "document_url": attachment,
                "document_sha256": "a" * 64,
                "parser_version": LEGACY_FILING_PARSER_VERSION,
            }
        ],
        columns=list(FILING_FACT_COLUMNS),
    )
    store = ImmutableCheckpointStore(tmp_path / "checkpoint")
    legacy_identity = filing_materialization._document_identity(
        source_commit=legacy_commit,
        symbol="688012",
        document_id=document_id,
        attachment_url=attachment,
        parser_version=LEGACY_FILING_PARSER_VERSION,
    )
    store.save(legacy_identity, frames={"facts": legacy_facts})

    class Downloaded:
        url = attachment
        retrieval_url = attachment
        sha256 = "b" * 64
        content = b"changed"

    monkeypatch.setattr(
        filing_materialization,
        "download_official_document",
        lambda url: Downloaded(),
    )
    result = filing_materialization.materialize_versioned_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-05-02",
        trading_dates=pd.date_range("2025-04-28", "2025-05-02", freq="B"),
        source_commit=current_commit,
        checkpoint_source_commit=legacy_commit,
        checkpoint_dir=tmp_path / "checkpoint",
        warmup_years=2,
    )
    assert result.coverage.iloc[0]["query_status"] == "FAILED"
    assert result.errors.iloc[0]["severity"] == "HARD_FAILURE"
