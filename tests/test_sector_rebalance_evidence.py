from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.sector_rebalance_evidence import (
    announcement_payload,
    extract_attachments,
    extract_effective_date,
    extract_index_changes_from_sheets,
    has_index_rows,
    parse_notice_rows,
)


def test_announcement_payload_is_searchable_and_bounded() -> None:
    payload = announcement_payload("创新药", page=2, rows=50)
    assert payload["lang"] == "cn"
    assert payload["searchInput"] == "创新药"
    assert payload["page"] == {"key": "", "order": None, "page": 2, "rows": 50, "sortBy": ""}


def test_parse_notice_rows_filters_dates_and_deduplicates() -> None:
    payload = {
        "code": 200,
        "data": [
            {"id": 1, "title": "关于调整指数样本的公告", "publishDate": "2023-11-24"},
            {"id": 1, "title": "关于调整指数样本的公告", "publishDate": "2023-11-24"},
            {"id": 2, "title": "older", "publishDate": "2018-11-01"},
        ],
    }
    rows = parse_notice_rows(payload, since="2019-01-01", until="2023-12-31")
    assert len(rows) == 1
    assert rows[0].notice_id == 1
    assert rows[0].publish_date == "2023-11-24"
    assert rows[0].detail_url.endswith("id=1")


def test_extract_attachment_and_effective_date() -> None:
    detail = {
        "id": 123,
        "title": "关于调整若干指数样本的公告",
        "publishDate": "2023-11-24",
        "content": "<p>本次调整将于2023年12月11日生效。</p>",
        "enclosureList": [
            {"fileName": "调整名单.xlsx", "fileUrl": "https://example.test/rebalance.xlsx"}
        ],
    }
    assert extract_effective_date(detail) == "2023-12-11"
    rows = extract_attachments(detail)
    assert len(rows) == 1
    assert rows[0].effective_date == "2023-12-11"
    assert rows[0].file_name == "调整名单.xlsx"


def test_extract_931152_rows_when_workbook_contains_many_indices() -> None:
    sheets = {
        "调入": pd.DataFrame(
            {
                "指数代码": ["000300", "931152", 931152],
                "证券代码": [600000, 688177, "688235"],
                "证券简称": ["浦发银行", "百奥泰", "百济神州"],
            }
        ),
        "调出": pd.DataFrame(
            {
                "指数代码": ["931152", "000905"],
                "证券代码": ["000661", "600519"],
                "证券简称": ["长春高新", "贵州茅台"],
            }
        ),
    }
    assert has_index_rows(sheets, index_code="931152") is True
    result = extract_index_changes_from_sheets(
        sheets,
        index_code="931152",
        effective_date="2023-12-11",
    )
    assert result[["change_type", "security_code"]].to_records(index=False).tolist() == [
        ("add", "688177"),
        ("add", "688235"),
        ("remove", "000661"),
    ]
    assert set(result["evidence_status"]) == {"official_attachment_row"}


def test_missing_index_is_empty_not_no_change() -> None:
    sheets = {
        "调入": pd.DataFrame({"指数代码": ["000300"], "证券代码": [600000]}),
        "调出": pd.DataFrame({"指数代码": ["000300"], "证券代码": [600519]}),
    }
    assert has_index_rows(sheets, index_code="931152") is False
    result = extract_index_changes_from_sheets(
        sheets,
        index_code="931152",
        effective_date="2023-12-11",
    )
    assert result.empty


def test_non_200_notice_payload_fails_closed() -> None:
    with pytest.raises(ValueError, match="not 200"):
        parse_notice_rows({"code": 500, "data": []})
