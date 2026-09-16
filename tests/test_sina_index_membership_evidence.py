from __future__ import annotations

from tech_sentiment.sina_index_membership_evidence import (
    parse_membership_intervals,
    related_url,
)


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
