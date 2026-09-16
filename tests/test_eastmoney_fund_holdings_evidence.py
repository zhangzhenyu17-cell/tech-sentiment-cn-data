from __future__ import annotations

from hashlib import sha256

from tech_sentiment.eastmoney_fund_holdings_evidence import (
    holdings_url,
    parse_eastmoney_envelope,
    parse_holdings_html,
)


def _table_rows(count: int) -> str:
    rows = []
    for idx in range(count):
        code = f"{600000 + idx:06d}"
        rows.append(
            "<tr><td>{rank}</td><td><a>{code}</a></td><td>示例{rank}</td><td>1.0%</td></tr>".format(
                rank=idx + 1,
                code=code,
            )
        )
    return "".join(rows)


def test_parse_eastmoney_envelope_decodes_content_and_years() -> None:
    html = '<div class="box"><h4>2023年2季度股票投资明细</h4></div>'
    escaped = html.replace('\\', '\\\\').replace('"', '\\"')
    text = f'var apidata={{content:"{escaped}",arryear:[2023,2022,2021]}};'
    content, years = parse_eastmoney_envelope(text)
    assert content == html
    assert years == (2023, 2022, 2021)


def test_q2_full_batch_is_candidate_but_q1_top_ten_is_not() -> None:
    html = (
        '<div class="box"><div class="boxitem"><h4>2023年1季度股票投资明细</h4>'
        f'<table><tbody>{_table_rows(10)}</tbody></table></div></div>'
        '<div class="box"><div class="boxitem"><h4>2023年2季度股票投资明细</h4>'
        f'<table><tbody>{_table_rows(12)}</tbody></table></div></div>'
    )
    batches = parse_holdings_html(
        html,
        fund_code="159992",
        source_url="https://example.test/holdings",
        response_sha256=sha256(b"response").hexdigest(),
    )
    assert [batch.report_date for batch in batches] == ["2023-03-31", "2023-06-30"]
    assert len(batches[0].symbols) == 10
    assert batches[0].anchor_candidate is False
    assert batches[0].evidence_kind.endswith("crosscheck_only")
    assert len(batches[1].symbols) == 12
    assert batches[1].anchor_candidate is True
    assert batches[1].evidence_kind.endswith("anchor_candidate")


def test_q4_full_batch_is_candidate() -> None:
    html = (
        '<div class="box"><h4>2022年第4季度股票投资明细</h4>'
        f'<table><tbody>{_table_rows(20)}</tbody></table></div>'
    )
    batch = parse_holdings_html(
        html,
        fund_code="159992",
        source_url="https://example.test/holdings",
        response_sha256="abc",
    )[0]
    assert batch.report_date == "2022-12-31"
    assert batch.anchor_candidate is True


def test_holdings_url_is_deterministic_and_year_scoped() -> None:
    url = holdings_url("159992", 2023, topline=100)
    assert url.startswith("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?")
    assert "code=159992" in url
    assert "year=2023" in url
    assert "topline=100" in url


def test_less_than_eleven_symbols_never_becomes_full_anchor() -> None:
    html = (
        '<div class="box"><h4>2023年4季度股票投资明细</h4>'
        f'<table><tbody>{_table_rows(10)}</tbody></table></div>'
    )
    batch = parse_holdings_html(
        html,
        fund_code="159992",
        source_url="https://example.test/holdings",
        response_sha256="abc",
    )[0]
    assert batch.anchor_candidate is False
