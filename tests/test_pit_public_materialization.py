import pandas as pd
import pytest

from tech_sentiment.pit_materialized_replay import (
    append_materialized_pit,
    assert_prefix_replay_equality,
    replay_materialized_pit_as_of,
)
from tech_sentiment.pit_public_materialization import (
    CNINFO_SOURCE_ID,
    classify_cninfo_title,
    major_negative_source_coverage_complete,
    materialize_cninfo_archive,
    normalize_cninfo_announcements,
    validate_materialized_pit_records,
)


def _calendar():
    return pd.to_datetime(
        [
            "2022-04-28",
            "2022-04-29",
            "2022-05-05",
            "2022-05-06",
            "2022-05-09",
            "2022-12-30",
        ]
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


def _normalize(raw: pd.DataFrame, captured_at: str = "2026-09-17T12:00:00Z") -> pd.DataFrame:
    return normalize_cninfo_announcements(
        raw,
        symbol="600276",
        query_start="2022-01-04",
        query_end="2022-12-30",
        trading_dates=_calendar(),
        captured_at=captured_at,
    )


def test_title_taxonomy_is_document_only_not_market_direction():
    assert classify_cninfo_title("2021年年度报告") == "FINANCIAL_REPORT_ANNOUNCEMENT"
    assert classify_cninfo_title("2022年度业绩预告") == "ISSUER_EARNINGS_FORECAST"
    assert classify_cninfo_title("关于重大诉讼事项的公告") == "LITIGATION_EVENT"
    assert classify_cninfo_title("风险提示公告") == "MAJOR_NEGATIVE_EVENT"
    assert classify_cninfo_title("关于战略合作协议的公告") == "BD_EVENT"


def test_preclose_precise_publication_is_usable_same_real_trading_date():
    raw = pd.DataFrame([_announcement("2021年年度报告", "121320", "2022-04-28 14:30:00")])
    out = _normalize(raw)
    row = out.iloc[0]
    assert row["event_date"] == pd.Timestamp("2022-04-28")
    assert row["evidence_available_date"] == pd.Timestamp("2022-04-28")
    assert "PRE_OR_AT_CLOSE_TIMESTAMP_SAME_TRADE_DATE" in row["provenance"]


def test_after_close_publication_moves_to_next_real_trading_date():
    raw = pd.DataFrame([_announcement("2021年年度报告", "121321", "2022-04-28 18:00:00")])
    out = _normalize(raw)
    row = out.iloc[0]
    assert row["evidence_id"] == "cninfo:121321"
    assert row["entity_id"] == "600276.SH"
    assert row["document_id"] == "121321"
    assert row["revision_id"] == "DOCUMENT:121321"
    assert row["source_identity"] == CNINFO_SOURCE_ID
    assert row["availability_state"] == "HISTORICAL_RECONSTRUCTABLE"
    assert row["event_date"] == pd.Timestamp("2022-04-28")
    assert row["evidence_available_date"] == pd.Timestamp("2022-04-29")
    assert "AFTER_CLOSE_NEXT_TRADE_DATE" in row["provenance"]


def test_date_only_publication_moves_to_next_real_trading_date():
    raw = pd.DataFrame([_announcement("日期粒度公告", "121322", "2022-04-28")])
    out = _normalize(raw)
    assert out.loc[0, "event_date"] == pd.Timestamp("2022-04-28")
    assert out.loc[0, "evidence_available_date"] == pd.Timestamp("2022-04-29")
    assert "DATE_ONLY_NEXT_TRADE_DATE" in out.loc[0, "provenance"]


def test_nontrading_day_publication_moves_to_next_real_trading_date():
    raw = pd.DataFrame([_announcement("周末公告", "121323", "2022-05-07 09:00:00")])
    out = _normalize(raw)
    assert out.loc[0, "event_date"] == pd.Timestamp("2022-05-07")
    assert out.loc[0, "evidence_available_date"] == pd.Timestamp("2022-05-09")
    assert "NON_TRADING_DAY_NEXT_TRADE_DATE" in out.loc[0, "provenance"]


def test_no_next_real_trading_date_fails_closed():
    raw = pd.DataFrame([_announcement("最后日公告", "121324", "2022-12-30 18:00:00")])
    with pytest.raises(ValueError, match="lacks next market date"):
        _normalize(raw)


def test_conflicting_duplicate_evidence_id_fails_closed():
    raw = pd.DataFrame(
        [
            _announcement("2021年年度报告", "121325", "2022-04-28 14:00:00"),
            _announcement("2021年年度报告（修订版）", "121325", "2022-04-28 14:00:00"),
        ]
    )
    normalized = []
    for i in range(2):
        normalized.append(
            _normalize(raw.iloc[[i]], captured_at=f"2026-09-17T12:0{i}:00Z")
        )
    combined = pd.concat(normalized, ignore_index=True)
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        validate_materialized_pit_records(combined)


def test_append_is_idempotent_and_conflicting_history_fails_closed():
    original = _normalize(
        pd.DataFrame([_announcement("原始公告", "121326", "2022-04-28 14:00:00")])
    )
    replay = append_materialized_pit(original, original.copy())
    assert len(replay) == 1

    conflict = original.copy()
    conflict.loc[0, "title"] = "篡改后的历史标题"
    with pytest.raises(ValueError, match="append-only PIT conflict"):
        append_materialized_pit(original, conflict)


def test_revision_safe_prefix_replay_excludes_later_document():
    original = _normalize(
        pd.DataFrame([_announcement("原公告", "121327", "2022-04-28 14:00:00")])
    )
    correction = _normalize(
        pd.DataFrame([_announcement("更正公告", "121328", "2022-05-06 14:00:00")])
    )
    ledger = append_materialized_pit(original, correction)
    apr29 = replay_materialized_pit_as_of(ledger, "2022-04-29")
    assert list(apr29["document_id"]) == ["121327"]
    may6 = replay_materialized_pit_as_of(ledger, "2022-05-06")
    assert list(may6["document_id"]) == ["121327", "121328"]
    assert_prefix_replay_equality(ledger, "2022-04-29")


def test_materializer_records_failed_symbol_query_in_coverage():
    def fetcher(**kwargs):
        if kwargs["symbol"] == "600276":
            return pd.DataFrame(
                [_announcement("2021年年度报告", "121329", "2022-04-28 14:00:00")]
            )
        raise RuntimeError("upstream unavailable")

    result = materialize_cninfo_archive(
        ["600276", "000001"],
        start_date="2022-01-04",
        end_date="2022-12-30",
        trading_dates=_calendar(),
        fetcher=fetcher,
    )
    assert result.summary["complete_symbol_queries"] == 1
    assert result.summary["failed_symbol_queries"] == 1
    assert result.summary["market_date_alignment"] == "REAL_TRADING_CALENDAR_CLOSE_BASED"
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
