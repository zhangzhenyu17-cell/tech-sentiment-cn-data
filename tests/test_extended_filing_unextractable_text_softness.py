from __future__ import annotations

import pandas as pd

from tech_sentiment import extended_filing_materialization as materializer


class _Downloaded:
    url = "https://static.cninfo.com.cn/finalpage/2025-08-29/1210000000.PDF"
    retrieval_url = url
    sha256 = "a" * 64
    content = b"official-image-only-pdf"


def _announcement() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "300763",
                "简称": "锦浪科技",
                "公告标题": "2025年半年度报告",
                "公告时间": "2025-08-29 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=300763&announcementId=1210000000&orgId=gssz0300763"
                ),
                "公告附件链接": _Downloaded.url,
            }
        ]
    )


def test_unextractable_official_pdf_is_soft_data_insufficiency(
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

    def no_text_layer(content: bytes) -> str:
        raise ValueError("official filing has no extractable text layer")

    monkeypatch.setattr(materializer, "extract_pdf_text", no_text_layer)

    result = materializer.materialize_extended_filing_facts(
        ["300763"],
        target_start_date="2025-01-01",
        end_date="2025-09-01",
        trading_dates=pd.date_range("2025-08-25", "2025-09-02", freq="B"),
        source_commit="1" * 40,
        checkpoint_dir=tmp_path / "checkpoint",
        hard_failure_circuit_breaker_threshold=1,
    )

    assert result.facts.empty
    assert result.coverage.iloc[0]["query_status"] == (
        "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
    )
    assert result.coverage.iloc[0]["soft_data_insufficient_documents"] == 1
    assert result.errors.iloc[0]["severity"] == "SOFT_DATA_INSUFFICIENCY"
    assert "no extractable text layer" in result.errors.iloc[0]["error"]
    assert result.summary["hard_failure_rows"] == 0
    assert result.summary["soft_data_insufficiency_rows"] == 1
    assert result.summary["circuit_breaker_tripped"] is False
    assert result.summary["outcome_read"] is False
    assert result.summary["evidence_qualification_changed"] is False
