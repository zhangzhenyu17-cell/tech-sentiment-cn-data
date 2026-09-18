from __future__ import annotations

import requests

import tech_sentiment.cninfo_direct as cninfo
from tech_sentiment.cninfo_direct import (
    CNINFO_STATIC_ORIGIN,
    CninfoProtocolError,
    _announcement_page,
    _attachment_url,
    _column_and_plate,
    _parse_stock_map,
    _parse_topsearch_org_id,
    fetch_cninfo_announcements_direct,
)


class _FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, code: str, org_id: str, announcements):
        self.code = code
        self.org_id = org_id
        self.announcements = announcements
        self.calls = []

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "data": dict(data or {}),
                "timeout": timeout,
            }
        )
        if "topSearch/query" in url:
            return _FakeResponse(
                [{"code": self.code, "orgId": self.org_id, "zwjc": "fixture"}]
            )
        if "hisAnnouncement/query" in url:
            return _FakeResponse(
                {
                    "announcements": self.announcements,
                    "hasMore": False,
                    "totalRecordNum": len(self.announcements or []),
                    "totalpages": 1,
                }
            )
        raise AssertionError(url)

    def close(self):
        return None


def _clear_caches():
    cninfo._ORG_ID_CACHE.clear()
    cninfo._STOCK_MAP_CACHE = None


def test_cninfo_attachment_url_preserves_exact_official_version_identity():
    relative = "finalpage/2026-04-30/1234567890.PDF"
    assert _attachment_url(relative) == f"{CNINFO_STATIC_ORIGIN}/{relative}"
    absolute = "https://static.cninfo.com.cn/finalpage/2026-04-30/abc.PDF"
    assert _attachment_url(absolute) == absolute
    assert _attachment_url("") == ""


def test_cninfo_identity_parsers_require_exact_unambiguous_code_match():
    assert _parse_topsearch_org_id(
        [{"code": "600519", "orgId": "gssh0600519"}], "600519"
    ) == "gssh0600519"
    assert _parse_stock_map(
        {"stockList": [{"code": "600519", "orgId": "gssh0600519"}]}
    )["600519"] == "gssh0600519"

    try:
        _parse_topsearch_org_id(
            [
                {"code": "600519", "orgId": "a"},
                {"code": "600519", "orgId": "b"},
            ],
            "600519",
        )
    except CninfoProtocolError:
        pass
    else:
        raise AssertionError("ambiguous orgId must fail closed")


def test_cninfo_exchange_column_is_not_hard_coded_to_szse():
    assert _column_and_plate("600519") == ("sse", "sh")
    assert _column_and_plate("688001") == ("sse", "sh")
    assert _column_and_plate("000538") == ("szse", "sz")
    assert _column_and_plate("300750") == ("szse", "sz")


def test_cninfo_query_uses_exact_orgid_exchange_column_and_attachment():
    _clear_caches()
    announcement = {
        "secCode": "600519",
        "secName": "贵州茅台",
        "announcementId": "1219506510",
        "announcementTitle": "2023年年度报告",
        "announcementTime": 1712102400000,
        "adjunctUrl": "finalpage/2024-04-03/1219506510.PDF",
    }
    session = _FakeSession(
        code="600519",
        org_id="gssh0600519",
        announcements=[announcement],
    )
    frame = fetch_cninfo_announcements_direct(
        symbol="600519",
        start_date="2024-04-02",
        end_date="2024-04-04",
        sleep_seconds=0,
        session=session,
    )
    assert len(frame) == 1
    assert frame.iloc[0]["公告附件链接"].endswith(
        "/finalpage/2024-04-03/1219506510.PDF"
    )
    query_call = next(
        call for call in session.calls if "hisAnnouncement/query" in call["url"]
    )
    assert query_call["data"]["stock"] == "600519,gssh0600519"
    assert query_call["data"]["column"] == "sse"
    assert query_call["data"]["plate"] == "sh"


def test_cninfo_valid_null_announcements_is_schemaful_empty_not_protocol_failure():
    _clear_caches()
    session = _FakeSession(
        code="000538",
        org_id="gssz0000538",
        announcements=None,
    )
    frame = fetch_cninfo_announcements_direct(
        symbol="000538",
        start_date="2024-04-10",
        end_date="2024-04-12",
        sleep_seconds=0,
        session=session,
    )
    assert frame.empty
    assert list(frame.columns) == [
        "代码",
        "简称",
        "公告标题",
        "公告时间",
        "公告链接",
        "公告附件链接",
    ]
    query_call = next(
        call for call in session.calls if "hisAnnouncement/query" in call["url"]
    )
    assert query_call["data"]["column"] == "szse"
    assert query_call["data"]["plate"] == "sz"


def test_announcement_payload_missing_field_fails_closed():
    try:
        _announcement_page({"hasMore": False})
    except CninfoProtocolError:
        pass
    else:
        raise AssertionError("missing announcements field must fail closed")
