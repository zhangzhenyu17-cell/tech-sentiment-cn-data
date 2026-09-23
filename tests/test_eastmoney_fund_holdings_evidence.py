from __future__ import annotations

from hashlib import sha256
from urllib.error import URLError

from tech_sentiment.eastmoney_fund_holdings_evidence import (
    WEIGHTED_CANDIDATE_BLOCKER,
    fetch_holdings_year,
    holdings_url,
    lookthrough_candidate_rows,
    parse_eastmoney_envelope,
    parse_holdings_html,
    parse_weighted_holdings_candidates,
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


def test_q2_full_report_is_candidate_set_but_q1_top_ten_is_not() -> None:
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
    assert batches[0].full_report_candidate_set is False
    assert batches[0].evidence_kind.endswith("crosscheck_only")
    assert len(batches[1].symbols) == 12
    assert batches[1].full_report_candidate_set is True
    assert batches[1].evidence_kind.endswith("candidate_set")


def test_q4_full_report_remains_candidate_set_not_index_anchor() -> None:
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
    assert batch.full_report_candidate_set is True
    assert "anchor" not in batch.evidence_kind


def test_holdings_url_is_deterministic_and_year_scoped() -> None:
    url = holdings_url("159992", 2023, topline=100)
    assert url.startswith("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?")
    assert "code=159992" in url
    assert "year=2023" in url
    assert "topline=100" in url


def test_less_than_eleven_symbols_never_becomes_full_report_candidate_set() -> None:
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
    assert batch.full_report_candidate_set is False


def test_weighted_candidates_parse_unambiguous_percent_without_granting_pit() -> None:
    html = (
        '<div class="box"><h4>2023年2季度股票投资明细</h4>'
        '<table><tbody>'
        '<tr><td>1</td><td><a>600276</a></td><td>恒瑞医药</td><td>7.25%</td></tr>'
        '<tr><td>2</td><td><a>688012</a></td><td>中微公司</td><td>3.50%</td></tr>'
        '</tbody></table></div>'
    )
    batch = parse_weighted_holdings_candidates(
        html,
        fund_code="159992",
        source_url="https://example.test/holdings",
        response_sha256="abc",
    )[0]

    assert batch.symbol_count == 2
    assert batch.weighted_symbol_count == 2
    assert batch.all_symbol_weights_parsed is True
    assert batch.pit_qualified is False
    assert [item.symbol for item in batch.positions] == ["600276", "688012"]
    assert [item.weight_fraction for item in batch.positions] == [0.0725, 0.035]

    rows = lookthrough_candidate_rows(batch)
    assert rows[0]["security_id"] == "CN:600276"
    assert rows[0]["weight_within_parent_candidate"] == 0.0725
    assert "weight_within_parent" not in rows[0]
    assert rows[0]["source_published_on"] is None
    assert rows[0]["pit_qualified"] is False
    assert rows[0]["qualification_blocker"] == WEIGHTED_CANDIDATE_BLOCKER


def test_ambiguous_percentage_row_keeps_symbol_but_does_not_invent_weight() -> None:
    html = (
        '<div class="box"><h4>2023年1季度股票投资明细</h4>'
        '<table><tbody>'
        '<tr><td>1</td><td><a>600276</a></td><td>恒瑞医药</td><td>7.25%</td><td>2.10%</td></tr>'
        '</tbody></table></div>'
    )
    batch = parse_weighted_holdings_candidates(
        html,
        fund_code="159992",
        source_url="https://example.test/holdings",
        response_sha256="abc",
    )[0]

    assert batch.symbol_count == 1
    assert batch.weighted_symbol_count == 0
    assert batch.all_symbol_weights_parsed is False
    assert lookthrough_candidate_rows(batch) == []


def test_fetch_holdings_year_retries_transient_transport_failures() -> None:
    class FakeHeaders:
        @staticmethod
        def get_content_charset() -> str:
            return "utf-8"

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b'var apidata={content:"<div></div>",arryear:[2023]};'

    calls = 0

    def flaky_opener(request, *, timeout):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise URLError("temporary disconnect")
        return FakeResponse()

    sleeps: list[float] = []
    text, url, digest = fetch_holdings_year(
        "159992",
        2023,
        retries=4,
        retry_backoff_seconds=0.25,
        sleep_fn=sleeps.append,
        opener=flaky_opener,
    )
    assert calls == 3
    assert sleeps == [0.25, 0.5]
    assert "arryear:[2023]" in text
    assert "year=2023" in url
    assert digest == sha256(text.encode("utf-8")).hexdigest()
