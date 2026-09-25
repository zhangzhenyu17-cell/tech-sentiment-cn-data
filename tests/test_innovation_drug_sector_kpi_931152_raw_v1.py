import json

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_sector_kpi_raw_v1 import (
    SECTOR_QUERY_KEYWORDS,
    build_931152_sector_raw_summary,
    filter_events_to_pit_membership,
    materialize_cninfo_market_keyword_archive,
    normalize_cninfo_sector_events,
    validate_931152_membership_scope,
)


def _raw_cninfo(title: str, code: str, announcement_id: str, when: str) -> dict[str, object]:
    return {
        "代码": code,
        "简称": "TEST",
        "公告标题": title,
        "公告时间": when,
        "公告链接": (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"announcementId={announcement_id}&orgId=test"
        ),
    }


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600276",
                "effective_start": "2024-01-01",
                "effective_end": "2026-09-11",
                "source_index": "931152",
                "universe_mode": "point_in_time",
                "source_scope": "OOS_QUALIFIED",
                "source_identity": "TEST_MEMBERSHIP",
                "source_member_file_sha256": "a" * 64,
                "source_reference": "artifact:test",
            },
            {
                "symbol": "688506",
                "effective_start": "2025-01-01",
                "effective_end": "2025-12-31",
                "source_index": "931152",
                "universe_mode": "point_in_time",
                "source_scope": "OOS_QUALIFIED",
                "source_identity": "TEST_MEMBERSHIP",
                "source_member_file_sha256": "b" * 64,
                "source_reference": "artifact:test",
            },
        ]
    )


def test_market_keyword_materialization_uses_query_only_for_transport_and_deduplicates() -> None:
    calls: list[str] = []

    def fetcher(**kwargs):
        calls.append(kwargs["keyword"])
        # Return the same announcement under every transport keyword to ensure
        # evidence identity, not query path, owns deduplication.
        row = _raw_cninfo(
            "恒瑞医药关于药品上市许可申请获受理的提示性公告",
            "600276",
            "1001",
            "2026-03-14",
        )
        return pd.DataFrame(
            [
                row,
                dict(row),
                _raw_cninfo(
                    "非候选公司关于获得药品注册批准的公告",
                    "000001",
                    "1002",
                    "2026-03-14",
                ),
            ]
        )

    calendar = pd.to_datetime(["2026-03-13", "2026-03-16", "2026-03-17"])
    result = materialize_cninfo_market_keyword_archive(
        keywords=("上市许可申请", "药品注册批准"),
        start_date="2026-03-01",
        end_date="2026-03-14",
        trading_dates=calendar,
        candidate_symbols=["600276"],
        fetcher=fetcher,
    )
    assert calls == ["上市许可申请", "药品注册批准"]
    assert len(result.pit_records) == 1
    assert result.pit_records.iloc[0]["entity_id"] == "600276.SH"
    assert result.pit_records.iloc[0]["document_id"] == "1001"
    assert str(pd.Timestamp(result.pit_records.iloc[0]["evidence_available_date"]).date()) == "2026-03-16"
    assert set(result.query_coverage["query_status"]) == {"COMPLETE_WINDOW"}
    assert result.query_coverage["raw_market_rows"].tolist() == [3, 3]
    assert result.query_coverage["deduplicated_market_rows"].tolist() == [2, 2]
    assert result.query_errors.empty


def test_market_keyword_json_decode_transport_failure_retries_exact_query() -> None:
    attempts = {"count": 0}

    def fetcher(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise json.JSONDecodeError("temporary non-json response", "", 0)
        return pd.DataFrame(
            [
                _raw_cninfo(
                    "恒瑞医药关于获得药品注册批准的公告",
                    "600276",
                    "1501",
                    "2026-03-14",
                )
            ]
        )

    calendar = pd.to_datetime(["2026-03-13", "2026-03-16"])
    result = materialize_cninfo_market_keyword_archive(
        keywords=("药品注册批准",),
        start_date="2026-03-01",
        end_date="2026-03-14",
        trading_dates=calendar,
        candidate_symbols=["600276"],
        fetcher=fetcher,
        fetch_attempts=2,
        retry_backoff_seconds=0,
    )
    assert attempts["count"] == 2
    assert result.query_coverage.iloc[0]["fetch_attempts_used"] == 2
    assert result.query_coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"
    assert result.pit_records["document_id"].astype(str).tolist() == ["1501"]
    assert result.query_errors.empty


def test_regulatory_marketing_license_title_cannot_be_misclassified_as_bd() -> None:
    calendar = pd.to_datetime(["2026-03-13", "2026-03-16"])

    def fetcher(**kwargs):
        return pd.DataFrame(
            [
                _raw_cninfo(
                    "恒瑞医药关于药品上市许可申请获受理的提示性公告",
                    "600276",
                    "2001",
                    "2026-03-14",
                )
            ]
        )

    raw = materialize_cninfo_market_keyword_archive(
        keywords=("上市许可申请",),
        start_date="2026-03-01",
        end_date="2026-03-14",
        trading_dates=calendar,
        candidate_symbols=["600276"],
        fetcher=fetcher,
    ).pit_records
    events = normalize_cninfo_sector_events(raw)
    assert events["event_type"].tolist() == ["MARKETING_APPLICATION_ACCEPTED"]
    assert events["event_family"].tolist() == ["CLINICAL_REGULATORY"]


def test_pit_membership_filter_uses_evidence_available_date_not_current_membership() -> None:
    calendar = pd.to_datetime(["2024-01-02", "2024-01-03", "2027-01-04"])

    def fetcher(**kwargs):
        return pd.DataFrame(
            [
                _raw_cninfo(
                    "恒瑞医药关于获得药品注册批准的公告",
                    "600276",
                    "3001",
                    "2024-01-02 14:00:00",
                ),
                _raw_cninfo(
                    "恒瑞医药关于获得药品注册批准的公告",
                    "600276",
                    "3002",
                    "2027-01-04 14:00:00",
                ),
            ]
        )

    raw = materialize_cninfo_market_keyword_archive(
        keywords=("药品注册批准",),
        start_date="2024-01-01",
        end_date="2027-01-04",
        trading_dates=calendar,
        candidate_symbols=["600276"],
        fetcher=fetcher,
    ).pit_records
    events = normalize_cninfo_sector_events(raw)
    filtered = filter_events_to_pit_membership(events, _membership())
    assert filtered["document_id"].astype(str).tolist() == ["3001"]
    assert filtered["pit_member_at_evidence_available_date"].astype(bool).all()


def test_membership_scope_rejects_overlap_and_current_backfill_shape() -> None:
    frame = _membership()
    duplicate = frame.iloc[[0]].copy()
    duplicate["effective_start"] = "2025-01-01"
    frame = pd.concat([frame, duplicate], ignore_index=True)
    with pytest.raises(ValueError, match="overlap"):
        validate_931152_membership_scope(frame)


def test_summary_requires_entire_frozen_query_set_and_remains_non_predictive() -> None:
    membership = validate_931152_membership_scope(_membership())
    empty_events = pd.DataFrame(columns=[
        "event_id", "domain_id", "entity_id", "event_family", "event_type",
        "event_date", "evidence_available_date", "source_identity", "provider",
        "document_id", "revision_id", "provenance", "ingestion_identity",
        "availability_state", "title", "source_url_identity", "direction_classified",
        "predictive_weight_assigned", "outcome_read", "membership_effective_start",
        "membership_effective_end", "membership_source_scope", "membership_source_identity",
        "membership_source_reference", "pit_member_at_evidence_available_date",
    ])
    coverage = pd.DataFrame(
        [
            {
                "keyword": keyword,
                "query_status": "COMPLETE_WINDOW",
                "raw_market_rows": 0,
                "candidate_union_rows": 0,
                "normalized_candidate_records": 0,
                "start_date": "2019-04-22",
                "end_date": "2026-09-11",
            }
            for keyword in SECTOR_QUERY_KEYWORDS
        ]
    )
    summary = build_931152_sector_raw_summary(
        events=empty_events,
        membership=membership,
        query_coverage=coverage,
        query_errors=pd.DataFrame(columns=["keyword", "error"]),
        start_date="2019-04-22",
        end_date="2026-09-11",
    )
    assert summary["query_count"] == len(SECTOR_QUERY_KEYWORDS)
    assert summary["complete_query_count"] == len(SECTOR_QUERY_KEYWORDS)
    assert summary["sector_kpi_formal_state"] == "DATA_INSUFFICIENT"
    assert summary["event_identity_is_direction"] is False
    assert summary["event_weight_defined"] is False
    assert summary["sector_score_defined"] is False
    assert summary["historical_outcomes_read"] is False
    assert summary["current_constituent_backfill_used"] is False

    with pytest.raises(ValueError, match="frozen query set"):
        build_931152_sector_raw_summary(
            events=empty_events,
            membership=membership,
            query_coverage=coverage.iloc[:-1].copy(),
            query_errors=pd.DataFrame(columns=["keyword", "error"]),
            start_date="2019-04-22",
            end_date="2026-09-11",
        )
