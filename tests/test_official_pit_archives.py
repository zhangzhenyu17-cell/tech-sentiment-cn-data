import pandas as pd

from tech_sentiment.official_pit_archives import (
    SSE_PROVIDER,
    SSE_SOURCE_ID,
    SSE_SPEC,
    SZSE_PROVIDER,
    SZSE_SOURCE_ID,
    SZSE_SPEC,
    materialize_official_archive,
    normalize_official_announcements,
)


def _calendar():
    return pd.to_datetime(["2022-04-28", "2022-04-29", "2022-05-05"])


def _frame(symbol: str, document_id: str, when: str = "2022-04-28") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "title": "2021年年度报告",
                "publication_time": when,
                "document_id": document_id,
                "source_url": f"https://example.invalid/{document_id}.pdf",
            }
        ]
    )


def test_sse_archive_has_independent_source_identity_and_conservative_date_only_rule():
    out = normalize_official_announcements(
        _frame("600276", "sse-doc-1"),
        spec=SSE_SPEC,
        symbol="600276",
        query_start="2022-01-04",
        query_end="2022-05-05",
        trading_dates=_calendar(),
        captured_at="2026-09-18T00:00:00Z",
    )
    row = out.iloc[0]
    assert row["entity_id"] == "600276.SH"
    assert row["source_identity"] == SSE_SOURCE_ID
    assert row["provider"] == SSE_PROVIDER
    assert row["document_id"] == "sse-doc-1"
    assert row["revision_id"] == "DOCUMENT:sse-doc-1"
    assert row["event_date"] == pd.Timestamp("2022-04-28")
    assert row["evidence_available_date"] == pd.Timestamp("2022-04-29")
    assert "DATE_ONLY_NEXT_TRADE_DATE" in row["provenance"]


def test_szse_archive_preserves_precise_preclose_availability():
    out = normalize_official_announcements(
        _frame("300750", "szse-doc-1", "2022-04-28 14:45:00"),
        spec=SZSE_SPEC,
        symbol="300750",
        query_start="2022-01-04",
        query_end="2022-05-05",
        trading_dates=_calendar(),
        captured_at="2026-09-18T00:00:00Z",
    )
    row = out.iloc[0]
    assert row["entity_id"] == "300750.SZ"
    assert row["source_identity"] == SZSE_SOURCE_ID
    assert row["provider"] == SZSE_PROVIDER
    assert row["event_date"] == pd.Timestamp("2022-04-28")
    assert row["evidence_available_date"] == pd.Timestamp("2022-04-28")
    assert "PRE_OR_AT_CLOSE_TIMESTAMP_SAME_TRADE_DATE" in row["provenance"]


def test_official_archive_query_failure_fails_closed_for_source_entity_pair():
    def failed_fetcher(**kwargs):
        raise RuntimeError("official source unavailable")

    result = materialize_official_archive(
        ["600276"],
        spec=SSE_SPEC,
        start_date="2022-01-04",
        end_date="2022-05-05",
        trading_dates=_calendar(),
        fetcher=failed_fetcher,
    )
    assert result.records.empty
    assert len(result.errors) == 1
    coverage = result.coverage.iloc[0]
    assert coverage["source_identity"] == SSE_SOURCE_ID
    assert coverage["entity_id"] == "600276.SH"
    assert coverage["query_status"] == "FAILED"


def test_official_archive_does_not_relabel_wrong_exchange_symbol():
    def fetcher(**kwargs):
        return _frame("300750", "wrong-market")

    result = materialize_official_archive(
        ["600276"],
        spec=SSE_SPEC,
        start_date="2022-01-04",
        end_date="2022-05-05",
        trading_dates=_calendar(),
        fetcher=fetcher,
    )
    assert result.records.empty
    assert result.coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"
    assert int(result.coverage.iloc[0]["records"]) == 0


def test_official_archive_retries_transient_transport_failure():
    calls = 0

    def fetcher(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(104, "reset")
        return _frame("600276", "sse-doc-retry")

    result = materialize_official_archive(
        ["600276"],
        spec=SSE_SPEC,
        start_date="2022-01-04",
        end_date="2022-05-05",
        trading_dates=_calendar(),
        fetcher=fetcher,
        retry_backoff_seconds=0,
    )
    assert calls == 2
    assert result.errors.empty
    assert result.coverage.iloc[0]["query_status"] == "COMPLETE_WINDOW"
