import pandas as pd
import pytest

from tech_sentiment.pit_public_materialization import (
    CNINFO_SOURCE_ID,
    classify_cninfo_title,
    major_negative_source_coverage_complete,
    materialize_cninfo_archive,
    normalize_cninfo_announcements,
    validate_materialized_pit_records,
)


def _announcement(title: str, announcement_id: str, when: str = "2022-04-28 18:00:00"):
    return {
        "代码": "600276",
        "简称": "恒瑞医药",
        "公告标题": title,
        "公告时间": when,
        "公告链接": (
            "http://www.cninfo.com.cn/new/disclosure/detail?"
            f"stockCode=600276&announcementId={announcement_id}&orgId=gssh0600276&"
            f"announcementTime={when}"
        ),
    }


def test_title_taxonomy_is_document_only_not_market_direction():
    assert classify_cninfo_title("2021年年度报告") == "FINANCIAL_REPORT_ANNOUNCEMENT"
    assert classify_cninfo_title("2022年度业绩预告") == "ISSUER_EARNINGS_FORECAST"
    assert classify_cninfo_title("关于重大诉讼事项的公告") == "LITIGATION_EVENT"
    assert classify_cninfo_title("风险提示公告") == "MAJOR_NEGATIVE_EVENT"
    assert classify_cninfo_title("关于战略合作协议的公告") == "BD_EVENT"


def test_cninfo_normalization_pins_document_identity_and_publication_date():
    raw = pd.DataFrame([_announcement("2021年年度报告", "121321")])
    out = normalize_cninfo_announcements(
        raw,
        symbol="600276",
        query_start="2022-01-04",
        query_end="2022-12-31",
        captured_at="2026-09-17T12:00:00Z",
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["evidence_id"] == "cninfo:121321"
    assert row["entity_id"] == "600276.SH"
    assert row["document_id"] == "121321"
    assert row["revision_id"] == "DOCUMENT:121321"
    assert row["source_identity"] == CNINFO_SOURCE_ID
    assert row["availability_state"] == "HISTORICAL_RECONSTRUCTABLE"
    assert row["event_date"] == pd.Timestamp("2022-04-28")
    assert row["evidence_available_date"] == pd.Timestamp("2022-04-28")
    assert "PUBLICATION_LEVEL_EVENT_DATE" in row["provenance"]


def test_conflicting_duplicate_evidence_id_fails_closed():
    raw = pd.DataFrame(
        [
            _announcement("2021年年度报告", "121321"),
            _announcement("2021年年度报告（修订版）", "121321"),
        ]
    )
    normalized = []
    for i in range(2):
        normalized.append(
            normalize_cninfo_announcements(
                raw.iloc[[i]],
                symbol="600276",
                query_start="2022-01-04",
                query_end="2022-12-31",
                captured_at=f"2026-09-17T12:0{i}:00Z",
            )
        )
    combined = pd.concat(normalized, ignore_index=True)
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        validate_materialized_pit_records(combined)


def test_materializer_records_failed_symbol_query_in_coverage():
    def fetcher(**kwargs):
        if kwargs["symbol"] == "600276":
            return pd.DataFrame([_announcement("2021年年度报告", "121321")])
        raise RuntimeError("upstream unavailable")

    result = materialize_cninfo_archive(
        ["600276", "000001"],
        start_date="2022-01-04",
        end_date="2022-12-31",
        fetcher=fetcher,
    )
    assert result.summary["complete_symbol_queries"] == 1
    assert result.summary["failed_symbol_queries"] == 1
    assert len(result.errors) == 1
    failed = result.coverage[result.coverage["entity_id"] == "000001.SZ"].iloc[0]
    assert failed["query_status"] == "FAILED"


def test_source_coverage_completion_does_not_imply_absence_of_negative_event():
    coverage = pd.DataFrame(
        [
            {
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "entity_id": "600276.SH",
                "coverage_start": "2022-01-04",
                "coverage_end": "2026-09-17",
                "query_status": "COMPLETE_WINDOW",
            },
            {
                "source_identity": "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
                "entity_id": "600276.SH",
                "coverage_start": "2022-01-04",
                "coverage_end": "2026-09-17",
                "query_status": "FAILED",
            },
        ]
    )
    assert major_negative_source_coverage_complete(
        coverage,
        entity_id="600276.SH",
        market_date="2025-01-02",
        required_source_identities=["CNINFO_ANNOUNCEMENT_ARCHIVE"],
    )
    assert not major_negative_source_coverage_complete(
        coverage,
        entity_id="600276.SH",
        market_date="2025-01-02",
        required_source_identities=[
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
        ],
    )
