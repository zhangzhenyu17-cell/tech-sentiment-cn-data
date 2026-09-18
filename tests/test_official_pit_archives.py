import json
from urllib.error import HTTPError

import pandas as pd

import tech_sentiment.official_pit_archives as official_pit_archives
from tech_sentiment.official_pit_archives import (
    SSE_PROVIDER,
    SSE_SOURCE_ID,
    SSE_SPEC,
    SSE_QUERY_URL,
    SSE_REFERER,
    fetch_sse_announcements,
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



def test_sse_issuer_browser_session_fallback_preserves_official_endpoint(monkeypatch):
    calls: list[tuple[str, str]] = []

    def blocked_urlopen(request, timeout=None):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

    class FakeResponse:
        def __init__(self, url: str, *, payload: dict[str, object] | None = None):
            self.url = url
            self.content = (
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None
                else b"<html>official SSE bootstrap</html>"
            )

        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(("get", url))
            if url == SSE_REFERER:
                assert kwargs["impersonate"] == "chrome"
                return FakeResponse(url)
            assert url.startswith(SSE_QUERY_URL + "?")
            assert kwargs["headers"]["Referer"] == SSE_REFERER
            assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
            return FakeResponse(
                url,
                payload={
                    "result": [
                        {
                            "SECURITY_CODE": "688981",
                            "TITLE": "2023年年度报告",
                            "SSEDATE": "2024-03-29",
                            "URL": (
                                "/disclosure/listedinfo/announcement/c/new/"
                                "2024-03-29/688981_fixture.pdf"
                            ),
                            "BULLETIN_ID": "fixture-688981-2023",
                        }
                    ]
                },
            )

        def close(self):
            calls.append(("close", "session"))

    monkeypatch.setattr(official_pit_archives, "urlopen", blocked_urlopen)

    frame = fetch_sse_announcements(
        symbol="688981",
        start_date="2024-03-01",
        end_date="2024-04-30",
        browser_session_factory=FakeSession,
    )

    assert list(frame["symbol"]) == ["688981"]
    assert list(frame["document_id"]) == ["fixture-688981-2023"]
    assert frame.loc[0, "source_url"].startswith("https://www.sse.com.cn/")
    assert calls[0] == ("get", SSE_REFERER)
    assert calls[1][1].startswith(SSE_QUERY_URL + "?")
    assert calls[-1] == ("close", "session")


def test_sse_issuer_browser_session_is_reused_across_year_windows(monkeypatch):
    query_calls: list[str] = []
    session_count = 0

    def blocked_urlopen(request, timeout=None):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

    class FakeResponse:
        def __init__(self, url: str, payload: dict[str, object] | None = None):
            self.url = url
            self.content = (
                json.dumps(payload).encode("utf-8")
                if payload is not None
                else b"<html>bootstrap</html>"
            )

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            nonlocal session_count
            session_count += 1

        def get(self, url, **kwargs):
            if url == SSE_REFERER:
                return FakeResponse(url)
            query_calls.append(url)
            return FakeResponse(url, {"result": []})

        def close(self):
            return None

    monkeypatch.setattr(official_pit_archives, "urlopen", blocked_urlopen)

    frame = fetch_sse_announcements(
        symbol="688981",
        start_date="2023-12-31",
        end_date="2024-01-02",
        browser_session_factory=FakeSession,
    )

    assert frame.empty
    assert session_count == 1
    assert len(query_calls) == 2
    assert all(url.startswith(SSE_QUERY_URL + "?") for url in query_calls)
