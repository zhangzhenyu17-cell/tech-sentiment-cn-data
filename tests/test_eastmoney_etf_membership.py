from __future__ import annotations

from tech_sentiment.eastmoney_etf_membership import (
    build_holdings_url,
    parse_apidata,
    parse_holding_periods,
)


def _row(seq: str, code: str) -> str:
    market = "1" if code.startswith("6") else "0"
    return (
        f"<tr><td>{seq}</td><td><a href='//quote.eastmoney.com/unify/r/{market}.{code}'>"
        f"{code}</a></td><td>示例</td><td>1.0%</td></tr>"
    )


def _block(label: str, as_of: str, rows: list[str], *, expandable: bool) -> str:
    control = "显示全部持仓明细>>" if expandable else "收起持仓明细>>"
    return (
        "<div class='box'><div class='boxitem w790'><h4>"
        f"{label}<label class='right lab2 xq505'>来源：天天基金 截止至："
        f"<font>{as_of}</font></label></h4><table><tbody>"
        + "".join(rows)
        + f"</tbody></table><div class='tfoot'>{control}</div></div></div>"
    )


def test_build_holdings_url_requests_expanded_june_december() -> None:
    url = build_holdings_url(year=2023)
    assert "code=159992" in url
    assert "year=2023" in url
    assert "month=6%2C12" in url
    assert "topline=100" in url


def test_parse_apidata_extracts_content_and_server_year() -> None:
    html = _block(
        "2023年4季度股票投资明细",
        "2023-12-31",
        [_row("1", "600001")],
        expandable=False,
    )
    payload = f'var apidata={{ content:"{html}",arryear:[2024,2023,2022],curyear:2023}};'
    content, years, current_year = parse_apidata(payload)
    assert content == html
    assert years == (2024, 2023, 2022)
    assert current_year == 2023


def test_full_period_requires_expansion_and_more_than_quarterly_cap() -> None:
    rows = [_row(str(i + 1), f"600{i:03d}") for i in range(16)]
    rows.append(_row("17*", "000001"))
    html = _block(
        "2023年2季度股票投资明细",
        "2023-06-30",
        rows,
        expandable=False,
    )
    periods = parse_holding_periods(html)
    assert len(periods) == 1
    period = periods[0]
    assert period.coverage_status == "candidate_full_portfolio"
    assert period.is_full_anchor_candidate is True
    assert period.fund_report_count == 16
    assert period.cross_reference_symbols == ("000001",)


def test_expandable_full_period_fails_closed() -> None:
    rows = [_row(str(i + 1), f"600{i:03d}") for i in range(20)]
    html = _block(
        "2023年4季度股票投资明细",
        "2023-12-31",
        rows,
        expandable=True,
    )
    period = parse_holding_periods(html)[0]
    assert period.coverage_status == "withheld_top_n"
    assert period.is_full_anchor_candidate is False


def test_quarterly_period_is_never_full_anchor() -> None:
    rows = [_row(str(i + 1), f"600{i:03d}") for i in range(20)]
    html = _block(
        "2023年3季度股票投资明细",
        "2023-09-30",
        rows,
        expandable=False,
    )
    period = parse_holding_periods(html)[0]
    assert period.coverage_status == "partial_disclosure_only"
