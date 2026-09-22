from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment import extended_filing_materialization as materializer


def _announcement() -> pd.DataFrame:
    document_id = "1210000000"
    return pd.DataFrame(
        [
            {
                "代码": "688012",
                "简称": "中微公司",
                "公告标题": "2025年半年度报告",
                "公告时间": "2025-08-29 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode=688012&announcementId={document_id}&orgId=gssh0600688"
                ),
                "公告附件链接": (
                    "https://static.cninfo.com.cn/finalpage/"
                    f"2025-08-29/{document_id}.PDF"
                ),
            }
        ]
    )


def _text() -> str:
    return """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    货币资金 12,345 11,111
    短期借款 2,000 1,500
    长期借款 4,000 3,500
    应付债券 500 450

    合并利润表 单位：人民币万元
    研发费用 888 777

    合并现金流量表 单位：人民币万元
    购建固定资产、无形资产和其他长期资产支付的现金 1,234 1,100
    期末现金及现金等价物余额 9,876 8,765
    """


class _Downloaded:
    url = (
        "https://static.cninfo.com.cn/finalpage/"
        "2025-08-29/1210000000.PDF"
    )
    retrieval_url = url
    sha256 = "a" * 64
    content = b"same-official-pdf"


def test_materializer_builds_raw_pit_rows_and_reuses_progress_checkpoint(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {"query": 0, "download": 0}

    def fetch(**kwargs):
        calls["query"] += 1
        return _announcement()

    def download(url):
        calls["download"] += 1
        return _Downloaded()

    monkeypatch.setattr(
        materializer,
        "fetch_cninfo_announcements_direct",
        fetch,
    )
    monkeypatch.setattr(materializer, "download_official_document", download)
    monkeypatch.setattr(materializer, "extract_pdf_text", lambda content: _text())

    trading_dates = pd.date_range("2025-08-25", "2025-09-02", freq="B")
    first = materializer.materialize_extended_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=trading_dates,
        source_commit="1" * 40,
        progress_checkpoint_source_commit="2" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
    )

    assert calls == {"query": 1, "download": 1}
    assert first.coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"
    assert first.summary["executed_symbol_queries"] == 1
    assert first.summary["executed_documents"] == 1
    assert first.summary["outcome_read"] is False
    assert first.summary["evidence_qualification_changed"] is False
    assert first.summary["cash_model_field_mapping_defined"] is False
    assert first.summary["debt_model_field_aggregation_defined"] is False
    assert first.summary["source_native_revision_sequence_qualified"] is False

    fact_types = set(first.facts["fact_type"])
    assert {
        "MONETARY_FUNDS",
        "CASH_AND_CASH_EQUIVALENTS_END",
        "CAPEX_CASH_PAID",
        "R_AND_D_EXPENSE",
        "SHORT_TERM_BORROWINGS",
        "LONG_TERM_BORROWINGS",
        "BONDS_PAYABLE",
    }.issubset(fact_types)
    assert "CASH" not in fact_types
    assert "DEBT" not in fact_types
    assert set(first.facts["document_sha256"]) == {"a" * 64}
    assert set(first.facts["document_id"]) == {"1210000000"}

    monkeypatch.setattr(
        materializer,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("progress query checkpoint should be reused")
        ),
    )
    monkeypatch.setattr(
        materializer,
        "download_official_document",
        lambda url: (_ for _ in ()).throw(
            AssertionError("progress document checkpoint should be reused")
        ),
    )
    second = materializer.materialize_extended_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=trading_dates,
        source_commit="3" * 40,
        progress_checkpoint_source_commit="2" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
    )

    assert second.summary["resumed_symbol_queries"] == 1
    assert second.summary["resumed_documents"] == 1
    assert second.summary["executed_symbol_queries"] == 0
    assert second.summary["executed_documents"] == 0
    pd.testing.assert_frame_equal(first.facts, second.facts)


def test_materializer_treats_absent_extended_line_items_as_soft_data_insufficiency(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        materializer,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: _announcement(),
    )
    monkeypatch.setattr(
        materializer,
        "download_official_document",
        lambda url: _Downloaded(),
    )
    monkeypatch.setattr(
        materializer,
        "extract_pdf_text",
        lambda content: (
            "2025年半年度报告\n"
            "合并利润表 单位：人民币万元\n"
            "营业收入 100 90\n"
        ),
    )

    result = materializer.materialize_extended_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=pd.date_range("2025-08-25", "2025-09-02", freq="B"),
        source_commit="4" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
    )

    assert result.facts.empty
    assert (
        result.coverage.iloc[0]["query_status"]
        == "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
    )
    assert result.errors.iloc[0]["severity"] == "SOFT_DATA_INSUFFICIENCY"
    assert result.summary["filing_fact_rows"] == 0
    assert result.summary["observed_fact_types"] == []


def test_materializer_missing_immutable_attachment_is_hard_failure(
    monkeypatch,
    tmp_path,
) -> None:
    raw = _announcement()
    raw.loc[:, "公告附件链接"] = ""
    monkeypatch.setattr(
        materializer,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: raw,
    )

    result = materializer.materialize_extended_filing_facts(
        ["688012"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=pd.date_range("2025-08-25", "2025-09-02", freq="B"),
        source_commit="5" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
    )

    assert result.coverage.iloc[0]["query_status"] == "FAILED"
    assert result.errors.iloc[0]["severity"] == "HARD_FAILURE"
    assert "MISSING_IMMUTABLE_ATTACHMENT_URL" in result.errors.iloc[0]["error"]


def test_materializer_hard_failure_circuit_breaker_stops_repeated_query_class(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {"query": 0}

    def fail_query(**kwargs):
        calls["query"] += 1
        raise RuntimeError("same deterministic provider failure")

    monkeypatch.setattr(
        materializer,
        "fetch_cninfo_announcements_direct",
        fail_query,
    )

    result = materializer.materialize_extended_filing_facts(
        ["688001", "688002", "688003", "688004"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=pd.date_range("2025-01-01", "2025-09-01", freq="B"),
        source_commit="6" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
        hard_failure_circuit_breaker_threshold=3,
    )

    assert calls["query"] == 3
    assert result.summary["completed_symbols"] == 3
    assert result.summary["hard_failure_rows"] == 3
    assert result.summary["circuit_breaker_tripped"] is True
    assert result.summary["circuit_breaker_signature"] == (
        "RuntimeError:same deterministic provider failure"
    )
    assert len(result.coverage) == 3
    assert result.coverage["query_status"].eq("FAILED").all()
