from urllib.parse import parse_qs, urlparse

import pandas as pd

from tech_sentiment.resumable_policy_archive import (
    POLICY_CHECKPOINT_VERSION,
    materialize_csrc_policy_archive_resumable,
)


_CHANNEL_IDS = {
    "c101953": "a" * 32,
    "c101954": "b" * 32,
    "c100040": "c" * 32,
    "c100039": "d" * 32,
}


def _metadata_payload(code: str) -> dict[str, object]:
    return {
        "code": 200,
        "results": {
            "channelLevel": [
                {
                    "channelCode": code,
                    "channelId": _CHANNEL_IDS[code],
                    "channelName": f"name-{code}",
                }
            ]
        },
    }


def _item(code: str, *, manuscript: str, when: str) -> dict[str, object]:
    return {
        "title": f"{code}-{manuscript}",
        "manuscriptId": manuscript,
        "publishedTimeStr": when,
        "url": f"//www.csrc.gov.cn/csrc/{code}/{manuscript}/content.shtml",
        "channelCodeName": code,
        "channelId": _CHANNEL_IDS[code],
    }


def _page_payload(
    code: str,
    *,
    page: int,
    manuscript: str,
    when: str,
    total: int = 2,
) -> dict[str, object]:
    return {
        "data": {
            "page": page,
            "rows": 1,
            "total": total,
            "channelId": _CHANNEL_IDS[code],
            "results": [_item(code, manuscript=manuscript, when=when)],
        }
    }


def _json_fixture(calls: list[str], *, total_drift_code: str | None = None):
    def fetcher(url: str) -> dict[str, object]:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/getLocalList":
            code = parse_qs(parsed.query)["channelCode"][0]
            return _metadata_payload(code)
        channel_id = parsed.path.rsplit("/", 1)[-1]
        code = next(code for code, value in _CHANNEL_IDS.items() if value == channel_id)
        page = int(parse_qs(parsed.query)["page"][0])
        if page == 1:
            return _page_payload(
                code,
                page=1,
                manuscript=f"{code}-new",
                when="2026-01-20 10:00:00",
                total=2,
            )
        if page == 2:
            return _page_payload(
                code,
                page=2,
                manuscript=f"{code}-old",
                when="2021-12-31 10:00:00",
                total=3 if code == total_drift_code else 2,
            )
        raise AssertionError(url)

    return fetcher


def test_policy_channel_pages_and_documents_resume_without_refetch(tmp_path):
    calls: list[str] = []
    article_calls: list[str] = []

    def article_fetcher(url: str) -> str:
        article_calls.append(url)
        return "<html><body>官方正文</body></html>"

    kwargs = dict(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        json_fetcher=_json_fixture(calls),
        article_fetcher=article_fetcher,
        page_size=1,
    )
    first = materialize_csrc_policy_archive_resumable(**kwargs)
    assert first.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert first.summary["checkpoint_schema"] == POLICY_CHECKPOINT_VERSION
    assert first.summary["executed_channels"] == 4
    assert first.summary["executed_pages"] == 8
    assert first.summary["executed_documents"] == 4
    assert len(first.records) == 4
    assert len(calls) == 12
    assert len(article_calls) == 4

    calls.clear()
    article_calls.clear()
    second = materialize_csrc_policy_archive_resumable(**kwargs)
    assert calls == []
    assert article_calls == []
    assert second.summary["resumed_channels"] == 4
    assert second.summary["resumed_pages"] == 8
    assert second.summary["resumed_documents"] == 4
    pd.testing.assert_frame_equal(first.records, second.records, check_dtype=False)
    pd.testing.assert_frame_equal(first.coverage, second.coverage, check_dtype=False)


def test_policy_total_drift_fails_closed(tmp_path):
    calls: list[str] = []
    result = materialize_csrc_policy_archive_resumable(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        json_fetcher=_json_fixture(calls, total_drift_code="c101953"),
        article_fetcher=lambda url: "official article",
        page_size=1,
    )
    orders = result.coverage[
        result.coverage["coverage_segment"].eq("CSRC_ORDERS")
    ].iloc[0]
    assert orders["query_status"] == "FAILED"
    assert result.summary["source_coverage_complete"] is False
    assert result.errors["error"].str.contains("total drift", regex=False).any()


def test_policy_duplicate_across_pages_fails_closed(tmp_path):
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, object]:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/getLocalList":
            code = parse_qs(parsed.query)["channelCode"][0]
            return _metadata_payload(code)
        channel_id = parsed.path.rsplit("/", 1)[-1]
        code = next(code for code, value in _CHANNEL_IDS.items() if value == channel_id)
        page = int(parse_qs(parsed.query)["page"][0])
        manuscript = f"{code}-dup" if code == "c101953" else f"{code}-{page}"
        when = "2026-01-20 10:00:00" if page == 1 else "2021-12-31 10:00:00"
        return _page_payload(
            code,
            page=page,
            manuscript=manuscript,
            when=when,
            total=2,
        )

    result = materialize_csrc_policy_archive_resumable(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        json_fetcher=fetcher,
        article_fetcher=lambda url: "official article",
        page_size=1,
    )
    orders = result.coverage[
        result.coverage["coverage_segment"].eq("CSRC_ORDERS")
    ].iloc[0]
    assert orders["query_status"] == "FAILED"
    assert result.errors["error"].str.contains("duplicate", case=False).any()


def test_policy_cross_page_order_drift_is_safe_under_full_enumeration(tmp_path):
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, object]:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/getLocalList":
            code = parse_qs(parsed.query)["channelCode"][0]
            return _metadata_payload(code)
        channel_id = parsed.path.rsplit("/", 1)[-1]
        code = next(code for code, value in _CHANNEL_IDS.items() if value == channel_id)
        page = int(parse_qs(parsed.query)["page"][0])
        when = "2026-01-20 10:00:00"
        if code == "c101953" and page == 2:
            when = "2026-01-21 10:00:00"
        elif page == 2:
            when = "2021-12-31 10:00:00"
        return _page_payload(
            code,
            page=page,
            manuscript=f"{code}-{page}",
            when=when,
            total=2,
        )

    result = materialize_csrc_policy_archive_resumable(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        json_fetcher=fetcher,
        article_fetcher=lambda url: "official article",
        page_size=1,
    )

    orders = result.coverage[
        result.coverage["coverage_segment"].eq("CSRC_ORDERS")
    ].iloc[0]
    assert orders["query_status"] == "COMPLETE_WINDOW"
    assert result.summary["source_coverage_complete"] is True
    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert len(result.errors) == 0
