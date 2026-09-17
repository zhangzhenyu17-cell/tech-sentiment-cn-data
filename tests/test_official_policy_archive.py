import pandas as pd

from tech_sentiment.official_policy_archive import (
    CSRC_LISTS,
    POLICY_SOURCE_ID,
    materialize_csrc_policy_archive,
    parse_csrc_list_page,
)


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


def test_policy_materializer_paginates_to_start_and_pins_article_hashes():
    pages: dict[str, str] = {}
    for segment, (base, _) in CSRC_LISTS.items():
        pages[base] = f"""
        <ul>
          <li><a href="/csrc/{segment.lower()}/new/content.shtml">{segment} 新规</a><span>2026-01-20</span></li>
          <li><a href="/csrc/{segment.lower()}/old/content.shtml">{segment} 历史</a><span>2021-12-31</span></li>
        </ul>
        """
        pages[f"https://www.csrc.gov.cn/csrc/{segment.lower()}/new/content.shtml"] = "article body"
    # The fixture URLs above do not have to mirror the list path; they only need
    # to stay on the official host, matching the parser's security contract.
    def fetcher(url: str) -> str:
        return pages[url]

    result = materialize_csrc_policy_archive(
        start_date="2022-01-04",
        end_date="2026-01-30",
        trading_dates=pd.bdate_range("2021-12-30", "2026-02-03"),
        fetcher=fetcher,
    )
    assert result.summary["source_coverage_complete"] is True
    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert set(result.records["source_identity"]) == {POLICY_SOURCE_ID}
    assert result.coverage["query_status"].eq("COMPLETE_WINDOW").all()
    assert len(result.records) == len(CSRC_LISTS)
