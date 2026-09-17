import re

import pandas as pd

from tech_sentiment.resumable_policy_archive import materialize_csrc_policy_archive_resumable


def test_policy_pages_and_documents_resume_without_refetch(tmp_path):
    calls: list[str] = []

    def fetcher(url: str) -> str:
        calls.append(url)
        if url.endswith("content.shtml") and "common_list" not in url:
            return "<html><body>官方正文</body></html>"
        match = re.search(r"/csrc/(c\d+)/common_list", url)
        assert match, url
        segment = match.group(1)
        return (
            "<html><body><div>"
            f'<a href="/csrc/{segment}/{segment}doc/content.shtml">{segment}监管通知</a>'
            "<span>2026-01-01</span>"
            "</div></body></html>"
        )

    kwargs = dict(
        start_date="2026-01-01",
        end_date="2026-01-05",
        trading_dates=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
        fetcher=fetcher,
    )
    first = materialize_csrc_policy_archive_resumable(**kwargs)
    first_calls = len(calls)
    assert first.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert first.summary["executed_pages"] == 4
    assert first.summary["executed_documents"] == 4
    assert len(first.records) == 4
    assert first_calls == 8

    calls.clear()
    second = materialize_csrc_policy_archive_resumable(**kwargs)
    assert calls == []
    assert second.summary["resumed_pages"] == 4
    assert second.summary["resumed_documents"] == 4
    pd.testing.assert_frame_equal(first.records, second.records, check_dtype=False)
    pd.testing.assert_frame_equal(first.coverage, second.coverage, check_dtype=False)
