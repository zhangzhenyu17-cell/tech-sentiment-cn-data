from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

import tech_sentiment.sina_index_membership_evidence as evidence
from tech_sentiment.sina_index_membership_evidence import (
    IndexMembershipInterval,
    parse_membership_intervals,
    related_url,
    summarize_interval_coverage,
)


def _valid_related_page(symbol: str = "600276") -> bytes:
    return f"""
    <html><body><h1>{symbol} 相关资料</h1><h3>所属指数</h3>
    <table>
      <tr><th>指数名称</th><th>指数代码</th><th>进入日期</th><th>退出日期</th></tr>
      <tr><td>CS创新药</td><td>931152</td><td>2019-04-22</td><td></td></tr>
    </table></body></html>
    """.encode("utf-8")


def test_parse_membership_interval_and_half_open_end_date() -> None:
    html = """
    <table>
      <tr><th>指数名称</th><th>指数代码</th><th>纳入日期</th><th>剔除日期</th></tr>
      <tr><td>CS创新药</td><td>931152</td><td>2020-06-15</td><td>2021-12-13</td></tr>
    </table>
    """
    intervals = parse_membership_intervals(
        html,
        symbol="600673",
        source_url="https://example.test/600673",
        response_sha256="abc",
    )
    assert len(intervals) == 1
    interval = intervals[0]
    assert interval.start_date == "2020-06-15"
    assert interval.end_date == "2021-12-13"
    assert interval.active_on("2021-12-12") is True
    assert interval.active_on("2021-12-13") is False


def test_current_member_without_end_date_stays_active() -> None:
    html = """
    <table><tr><td>CS创新药</td><td>931152</td><td>2019-04-22</td><td></td></tr></table>
    """
    interval = parse_membership_intervals(
        html,
        symbol="600276",
        source_url="https://example.test/600276",
        response_sha256="abc",
    )[0]
    assert interval.active_on("2019-04-22") is True
    assert interval.active_on("2026-09-17") is True


def test_unrelated_index_rows_are_ignored() -> None:
    html = """
    <table>
      <tr><td>沪深300</td><td>000300</td><td>2019-01-01</td><td></td></tr>
    </table>
    """
    assert parse_membership_intervals(
        html,
        symbol="600276",
        source_url="https://example.test/600276",
        response_sha256="abc",
    ) == []


def test_prefixed_currency_variant_code_is_not_target_index() -> None:
    html = """
    <table>
      <tr><td>CS创新药</td><td>931152</td><td>2019-04-22</td><td></td></tr>
      <tr><td>CS创新药USD</td><td>931152USD200</td><td>2025-06-23</td><td></td></tr>
      <tr><td>CS创新药(全)USD</td><td>931152USD210</td><td>2025-06-23</td><td></td></tr>
    </table>
    """
    intervals = parse_membership_intervals(
        html,
        symbol="000513",
        source_url="https://example.test/000513",
        response_sha256="abc",
    )
    assert len(intervals) == 1
    assert intervals[0].index_name == "CS创新药"
    assert intervals[0].start_date == "2019-04-22"


def test_duplicate_rendering_is_deduplicated() -> None:
    row = "<tr><td>CS创新药</td><td>931152</td><td>2020-06-15</td><td>2021-12-13</td></tr>"
    html = f"<table>{row}{row}</table>"
    intervals = parse_membership_intervals(
        html,
        symbol="600673",
        source_url="https://example.test/600673",
        response_sha256="abc",
    )
    assert len(intervals) == 1


def test_related_url_rejects_non_six_digit_symbol() -> None:
    assert related_url("600276").endswith("stockid/600276.phtml")
    try:
        related_url("sh600276")
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid symbol to fail closed")


def test_interval_coverage_does_not_count_empty_successes_as_evidence() -> None:
    interval = IndexMembershipInterval(
        symbol="600276",
        index_code="931152",
        index_name="CS创新药",
        start_date="2019-04-22",
        end_date="",
        source_url="https://example.test/600276",
        response_sha256="abc",
    )
    coverage = summarize_interval_coverage(
        {
            "600276": [interval],
            "600380": [],
            "600196": [interval, interval],
        },
        candidate_symbols={"600276", "600380", "600196", "000513"},
    )
    assert coverage == {
        "candidate_symbols": 4,
        "symbols_with_intervals": 2,
        "symbols_without_intervals": 2,
        "interval_rows": 3,
    }


def test_fetch_related_page_retries_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []
    body = _valid_related_page()

    class _Headers:
        @staticmethod
        def get_content_charset() -> str:
            return "utf-8"

    class _Response:
        headers = _Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return body

    def _urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("transient disconnect")
        return _Response()

    monkeypatch.setattr(evidence, "urlopen", _urlopen)
    text, url, digest = evidence.fetch_related_page(
        "600276",
        retries=2,
        retry_backoff_seconds=0.5,
        sleep_fn=sleeps.append,
        browser_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback not expected")),
    )

    assert attempts == 3
    assert sleeps == pytest.approx([0.8, 1.3])
    assert "所属指数" in text
    assert url.endswith("stockid/600276.phtml")
    assert len(digest) == 64


def test_fetch_related_page_uses_browser_fallback_after_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    fallback_calls: list[tuple[str, int]] = []
    sleeps: list[float] = []

    def _urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 403, "blocked", hdrs=None, fp=None)

    def _browser(url: str, *, timeout: int):
        fallback_calls.append((url, timeout))
        return _valid_related_page(), "utf-8"

    monkeypatch.setattr(evidence, "urlopen", _urlopen)
    text, url, digest = evidence.fetch_related_page(
        "600276",
        retries=4,
        sleep_fn=sleeps.append,
        browser_fetcher=_browser,
    )

    assert attempts == 1
    assert len(fallback_calls) == 1
    assert fallback_calls[0][0] == url
    assert fallback_calls[0][1] == 20
    assert sleeps == []
    assert "931152" in text
    assert len(digest) == 64


def test_browser_fallback_rejects_challenge_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(request, timeout):
        raise URLError("hosted-runner path blocked")

    def _browser(url: str, *, timeout: int):
        return b"<html><body>captcha access denied 600276</body></html>", "utf-8"

    monkeypatch.setattr(evidence, "urlopen", _urlopen)
    with pytest.raises(RuntimeError, match="browser=ValueError"):
        evidence.fetch_related_page(
            "600276",
            retries=0,
            sleep_fn=lambda _: None,
            browser_fetcher=_browser,
        )


def test_primary_transport_rejects_semantically_invalid_nonempty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Headers:
        @staticmethod
        def get_content_charset() -> str:
            return "utf-8"

    class _Response:
        headers = _Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return b"<html><body>temporary service page 600276</body></html>"

    monkeypatch.setattr(evidence, "urlopen", lambda request, timeout: _Response())
    with pytest.raises(RuntimeError, match="browser=ValueError"):
        evidence.fetch_related_page(
            "600276",
            retries=0,
            browser_fetcher=lambda url, timeout: (b"still blocked", "utf-8"),
        )
