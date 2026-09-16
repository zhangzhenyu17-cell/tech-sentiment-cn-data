from __future__ import annotations

import pandas as pd

from tech_sentiment.csi_adjustment_evidence import (
    _request_safe_url,
    attachment_refs_from_detail,
    canonicalize_attachment_url,
    extract_effective_date,
    extract_index_changes_from_sheets,
    parse_announcement_search,
)


def test_parse_announcement_search_filters_to_design_window_and_theme() -> None:
    payload = {
        "code": 200,
        "data": [
            {
                "id": 1001,
                "title": "关于部分指数定期调整结果的公告",
                "publishDate": "2023-11-24",
                "theme": "指数调样",
            },
            {
                "id": 1002,
                "title": "holdout notice",
                "publishDate": "2024-05-31",
                "theme": "指数调样",
            },
            {
                "id": 1003,
                "title": "中证创新药产业指数新闻",
                "publishDate": "2021-08-11",
                "theme": "其他",
            },
        ],
    }
    rows = parse_announcement_search(payload, search_term="931152")
    assert [row.notice_id for row in rows] == [1001]
    assert rows[0].detail_url.endswith("id=1001")


def test_attachment_refs_extracts_effective_date_and_relative_url() -> None:
    detail = {
        "id": 2001,
        "title": "关于指数样本调整的公告",
        "publishDate": "2023-11-24",
        "content": "<p>相关调整将于2023年12月11日正式生效。</p>",
        "enclosureList": [
            {
                "fileName": "调整名单.xlsx",
                "fileUrl": "/uploads/example/adjustment.xlsx",
            }
        ],
    }
    refs = attachment_refs_from_detail(detail)
    assert len(refs) == 1
    assert refs[0].effective_date == "2023-12-11"
    assert refs[0].file_url == "https://www.csindex.com.cn/uploads/example/adjustment.xlsx"


def test_attachment_refs_falls_back_to_content_href_and_deduplicates() -> None:
    detail = {
        "id": 2002,
        "title": "关于调整沪深300和中证香港100等指数样本股的公告",
        "publishDate": "2020-06-01",
        "content": (
            '<p><a href="/notice/202006/中证指数调入调出名单.xlsx">下载附件</a></p>'
            "<p><a href='https://example.com/not-official.xlsx'>外部链接</a></p>"
        ),
        "enclosureList": [],
    }
    refs = attachment_refs_from_detail(detail)
    assert len(refs) == 1
    assert refs[0].file_url == "https://www.csindex.com.cn/notice/202006/中证指数调入调出名单.xlsx"
    assert refs[0].file_name == "中证指数调入调出名单.xlsx"


def test_unicode_attachment_url_is_encoded_for_http_request() -> None:
    canonical = canonicalize_attachment_url(
        "https://oss-ch.csindex.com.cn/notice/20211130195824-中证指数调入调出名单.xlsx"
    )
    request_url = _request_safe_url(canonical)
    assert "中证" not in request_url
    assert "%E4%B8%AD%E8%AF%81" in request_url
    assert request_url.startswith("https://oss-ch.csindex.com.cn/")


def test_non_csindex_attachment_host_is_rejected() -> None:
    assert canonicalize_attachment_url("https://example.com/adjustment.xlsx") == ""


def test_extract_effective_date_accepts_implementation_wording() -> None:
    assert (
        extract_effective_date("<div>于2022年6月13日起正式实施。</div>")
        == "2022-06-13"
    )


def test_extract_index_changes_from_offset_header_and_mixed_indices() -> None:
    additions = pd.DataFrame(
        [
            ["说明", None, None],
            ["指数代码", "证券代码", "证券简称"],
            [931152, 688235, "示例A"],
            ["000300", "600519", "示例B"],
            ["931152", "002821", "示例C"],
        ]
    )
    removals = pd.DataFrame(
        [
            ["指数代码", "证券代码", "证券简称"],
            ["931152", "600721", "示例D"],
            ["000905", "600000", "示例E"],
        ]
    )
    changes = extract_index_changes_from_sheets(
        {"调入": additions, "调出": removals},
        index_code="931152",
    )
    assert changes == {
        "add": ["002821", "688235"],
        "remove": ["600721"],
    }


def test_missing_change_sheets_fail_closed_to_empty_changes() -> None:
    changes = extract_index_changes_from_sheets(
        {"说明": pd.DataFrame([["not evidence"]])},
        index_code="931152",
    )
    assert changes == {"add": [], "remove": []}
