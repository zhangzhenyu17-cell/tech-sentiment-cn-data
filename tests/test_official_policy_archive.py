from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from tech_sentiment.official_policy_archive import (
    CSRC_CHANNELS,
    _fetch_json,
    _fetch_text,
    POLICY_SOURCE_ID,
    PolicyChannel,
    materialize_csrc_policy_archive,
    parse_csrc_list_page,
    parse_csrc_search_page,
    resolve_csrc_channel,
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


def _item(
    code: str,
    *,
    manuscript: str,
    when: str,
    title: str | None = None,
    url: str | None = None,
) -> dict[str, object]:
    return {
        "title": title or f"{code}-{manuscript}",
        "manuscriptId": manuscript,
        "publishedTimeStr": when,
        "url": url or f"//www.csrc.gov.cn/csrc/{code}/{manuscript}/content.shtml",
        "channelCodeName": code,
        "channelId": _CHANNEL_IDS[code],
    }


def _page_payload(
    code: str,
    *,
    page: int,
    items: list[dict[str, object]],
    total: int | None = None,
    rows: int | None = None,
) -> dict[str, object]:
    return {
        "data": {
            "page": page,
            "rows": len(items) if rows is None else rows,
            "total": len(items) if total is None else total,
            "channelId": _CHANNEL_IDS[code],
            "results": items,
        }
    }


def _fixture_json_fetcher(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.path == "/getLocalList":
        code = parse_qs(parsed.query)["channelCode"][0]
        return _metadata_payload(code)
    if parsed.path.startswith("/searchList/"):
        channel_id = parsed.path.rsplit("/", 1)[-1]
        code = next(code for code, value in _CHANNEL_IDS.items() if value == channel_id)
        page = int(parse_qs(parsed.query)["page"][0])
        assert page == 1
        return _page_payload(
            code,
            page=1,
            total=2,
            items=[
                _item(code, manuscript=f"{code}-new", when="2026-01-20 10:00:00"),
                _item(code, manuscript=f"{code}-old", when="2021-12-31 10:00:00"),
            ],
        )
    raise AssertionError(url)




def test_csrc_json_timeout_uses_same_official_https_browser_fallback():
    calls: list[tuple[str, str]] = []

    def opener(request, timeout):
        calls.append(("urllib", request.full_url))
        raise TimeoutError("timed out")

    class BrowserResponse:
        url = "https://www.csrc.gov.cn/getLocalList?channelCode=c101953"
        content = b'{"code":200,"results":{"channelLevel":[]}}'

        def raise_for_status(self):
            return None

    def browser_get(url, **kwargs):
        calls.append(("browser", url))
        assert kwargs["impersonate"] == "chrome"
        return BrowserResponse()

    payload = _fetch_json(
        "https://www.csrc.gov.cn/getLocalList?channelCode=c101953",
        timeout=10.0,
        opener=opener,
        browser_get=browser_get,
    )

    assert payload["code"] == 200
    assert calls == [
        ("urllib", "https://www.csrc.gov.cn/getLocalList?channelCode=c101953"),
        ("browser", "https://www.csrc.gov.cn/getLocalList?channelCode=c101953"),
    ]


def test_csrc_text_timeout_uses_same_official_https_browser_fallback():
    def opener(request, timeout):
        raise TimeoutError("timed out")

    class BrowserResponse:
        url = "https://www.csrc.gov.cn/csrc/c101954/example/content.shtml"
        content = "官方正文".encode("utf-8")

        def raise_for_status(self):
            return None

    text = _fetch_text(
        "https://www.csrc.gov.cn/csrc/c101954/example/content.shtml",
        timeout=10.0,
        opener=opener,
        browser_get=lambda url, **kwargs: BrowserResponse(),
    )
    assert text == "官方正文"

def test_parse_csrc_list_page_requires_dated_official_content_links():
    html = """
    <ul>
      <li><a href="/csrc/c101954/c123/content.shtml">关于某项监管规定的公告</a><span>2026-05-10</span></li>
      <li><a href="/csrc/c101954/c124/content.shtml">行政处罚决定</a><span>2026年05月09日</span></li>
    </ul>
    """
    entries, undated = parse_csrc_list_page(
        html,
        page_url="https://www.csrc.gov.cn/csrc/c101954/common_list.shtml",
        segment="CSRC_ANNOUNCEMENTS",
        default_evidence_type="REGULATORY_EVENT",
    )
    assert undated == 0
    assert [entry.publication_date for entry in entries] == [
        pd.Timestamp("2026-05-10"),
        pd.Timestamp("2026-05-09"),
    ]


def test_official_channel_metadata_and_search_page_are_identity_bound():
    channel = resolve_csrc_channel(
        segment="CSRC_ORDERS",
        channel_code="c101953",
        default_evidence_type="REGULATORY_EVENT",
        payload=_metadata_payload("c101953"),
    )
    assert channel.channel_id == "a" * 32
    payload = _page_payload(
        "c101953",
        page=1,
        total=2,
        items=[
            _item("c101953", manuscript="2", when="2026-02-01 12:00:00"),
            _item("c101953", manuscript="1", when="2025-12-31 19:00:00"),
        ],
    )
    entries, meta = parse_csrc_search_page(
        payload,
        channel=channel,
        requested_page=1,
        requested_page_size=2,
    )
    assert meta == {"page": 1, "rows": 2, "returned": 2, "total": 2}
    assert [entry.manuscript_id for entry in entries] == ["2", "1"]
    assert entries[0].url.startswith("https://www.csrc.gov.cn/")


def test_search_page_rejects_page_drift_and_nonofficial_document_url():
    channel = PolicyChannel(
        segment="CSRC_ORDERS",
        channel_code="c101953",
        channel_id="a" * 32,
        channel_name="orders",
        default_evidence_type="REGULATORY_EVENT",
    )
    wrong_page = _page_payload(
        "c101953",
        page=2,
        items=[_item("c101953", manuscript="x", when="2026-01-01 10:00:00")],
    )
    with pytest.raises(ValueError, match="page mismatch"):
        parse_csrc_search_page(
            wrong_page,
            channel=channel,
            requested_page=1,
            requested_page_size=1,
        )

    bad_url = _page_payload(
        "c101953",
        page=1,
        items=[
            _item(
                "c101953",
                manuscript="x",
                when="2026-01-01 10:00:00",
                url="https://example.com/csrc/c101953/x/content.shtml",
            )
        ],
    )
    with pytest.raises(ValueError, match="official HTTPS host"):
        parse_csrc_search_page(
            bad_url,
            channel=channel,
            requested_page=1,
            requested_page_size=1,
        )




def test_search_page_accepts_server_capacity_larger_than_actual_results():
    channel = PolicyChannel(
        segment="CSRC_ORDERS",
        channel_code="c101953",
        channel_id="a" * 32,
        channel_name="orders",
        default_evidence_type="REGULATORY_EVENT",
    )
    items = [
        _item(
            "c101953",
            manuscript=f"m{index}",
            when=f"2026-01-{index + 1:02d} 10:00:00",
        )
        for index in range(14)
    ]
    payload = _page_payload(
        "c101953",
        page=7,
        total=352,
        rows=20,
        items=items,
    )

    entries, meta = parse_csrc_search_page(
        payload,
        channel=channel,
        requested_page=7,
        requested_page_size=50,
    )

    assert len(entries) == 14
    assert meta == {
        "page": 7,
        "rows": 20,
        "returned": 14,
        "total": 352,
    }


def test_search_page_rejects_invalid_server_capacity_contract():
    channel = PolicyChannel(
        segment="CSRC_ORDERS",
        channel_code="c101953",
        channel_id="a" * 32,
        channel_name="orders",
        default_evidence_type="REGULATORY_EVENT",
    )
    item = _item(
        "c101953",
        manuscript="m1",
        when="2026-01-01 10:00:00",
    )

    too_large_capacity = _page_payload(
        "c101953",
        page=1,
        total=1,
        rows=51,
        items=[item],
    )
    with pytest.raises(ValueError, match="exceeds requested page size"):
        parse_csrc_search_page(
            too_large_capacity,
            channel=channel,
            requested_page=1,
            requested_page_size=50,
        )

    too_many_results = _page_payload(
        "c101953",
        page=1,
        total=2,
        rows=1,
        items=[
            item,
            _item(
                "c101953",
                manuscript="m2",
                when="2026-01-02 10:00:00",
            ),
        ],
    )
    with pytest.raises(ValueError, match="exceed effective page capacity"):
        parse_csrc_search_page(
            too_many_results,
            channel=channel,
            requested_page=1,
            requested_page_size=50,
        )

def test_policy_materializer_uses_official_json_archive_and_pins_article_hashes():
    result = materialize_csrc_policy_archive(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        json_fetcher=_fixture_json_fetcher,
        article_fetcher=lambda url: f"official article {url}",
    )
    assert result.summary["source_coverage_complete"] is True
    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert result.summary["archive_protocol"] == "OFFICIAL_CSRC_GETLOCALLIST_SEARCHLIST_JSON_V4_SERVER_PAGE_CAPACITY"
    assert set(result.records["source_identity"]) == {POLICY_SOURCE_ID}
    assert result.coverage["query_status"].eq("COMPLETE_WINDOW").all()
    assert len(result.records) == len(CSRC_CHANNELS)
    assert result.records["provenance"].str.contains(
        "OFFICIAL_CSRC_JSON_CHANNEL_API", regex=False
    ).all()


def test_policy_document_failure_invalidates_its_segment_coverage():
    failed_code = "c101954"

    def article_fetcher(url: str) -> str:
        if f"/{failed_code}/" in url:
            raise RuntimeError("fixture document unavailable")
        return "official article"

    result = materialize_csrc_policy_archive(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        json_fetcher=_fixture_json_fetcher,
        article_fetcher=article_fetcher,
    )
    row = result.coverage[
        result.coverage["coverage_segment"].eq("CSRC_ANNOUNCEMENTS")
    ].iloc[0]
    assert row["query_status"] == "FAILED"
    assert result.summary["source_coverage_complete"] is False
    assert result.summary["readiness_state"] == "PARTIAL_COVERAGE"
    assert result.summary["document_failure_invalidates_segment_coverage"] is True



def test_search_page_accepts_unordered_publication_times_when_identity_is_valid():
    channel = PolicyChannel(
        segment="CSRC_ORDERS",
        channel_code="c101953",
        channel_id="a" * 32,
        channel_name="orders",
        default_evidence_type="REGULATORY_EVENT",
    )
    payload = _page_payload(
        "c101953",
        page=1,
        total=3,
        items=[
            _item("c101953", manuscript="new", when="2026-01-20 10:00:00"),
            _item("c101953", manuscript="old", when="2023-01-01 10:00:00"),
            _item("c101953", manuscript="middle", when="2025-06-01 10:00:00"),
        ],
    )

    entries, meta = parse_csrc_search_page(
        payload,
        channel=channel,
        requested_page=1,
        requested_page_size=3,
    )

    assert meta["total"] == 3
    assert [entry.manuscript_id for entry in entries] == ["new", "old", "middle"]


def test_policy_materializer_enumerates_full_advertised_total_before_date_filter():
    page_calls: list[tuple[str, int]] = []

    def fetcher(url: str) -> dict[str, object]:
        parsed = urlparse(url)
        if parsed.path == "/getLocalList":
            code = parse_qs(parsed.query)["channelCode"][0]
            return _metadata_payload(code)
        channel_id = parsed.path.rsplit("/", 1)[-1]
        code = next(code for code, value in _CHANNEL_IDS.items() if value == channel_id)
        page = int(parse_qs(parsed.query)["page"][0])
        page_calls.append((code, page))
        if page == 1:
            return _page_payload(
                code,
                page=1,
                total=4,
                items=[
                    _item(code, manuscript=f"{code}-new", when="2026-01-20 10:00:00"),
                    _item(code, manuscript=f"{code}-prewindow", when="2021-12-31 10:00:00"),
                ],
            )
        if page == 2:
            return _page_payload(
                code,
                page=2,
                total=4,
                items=[
                    _item(code, manuscript=f"{code}-middle", when="2025-06-01 10:00:00"),
                    _item(code, manuscript=f"{code}-older", when="2023-05-01 10:00:00"),
                ],
            )
        raise AssertionError(url)

    result = materialize_csrc_policy_archive(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        json_fetcher=fetcher,
        article_fetcher=lambda url: "official article",
        page_size=2,
    )

    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert result.summary["source_coverage_complete"] is True
    assert len(result.records) == len(CSRC_CHANNELS) * 3
    assert all((code, 2) in page_calls for code in _CHANNEL_IDS)
