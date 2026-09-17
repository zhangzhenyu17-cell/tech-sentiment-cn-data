from __future__ import annotations

from functools import lru_cache
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_MAP_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_REFERER = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
CNINFO_STATIC_ORIGIN = "https://static.cninfo.com.cn"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _read_json(request: Request, timeout: float) -> dict[str, object]:
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed official HTTPS endpoints
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("CNINFO response must be a JSON object")
    return payload


@lru_cache(maxsize=1)
def _stock_org_map() -> dict[str, str]:
    request = Request(
        CNINFO_STOCK_MAP_URL,
        headers={"User-Agent": _USER_AGENT, "Referer": CNINFO_REFERER},
        method="GET",
    )
    payload = _read_json(request, 30.0)
    rows = payload.get("stockList")
    if not isinstance(rows, list):
        raise ValueError("CNINFO stock map lacks stockList")
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = "".join(ch for ch in str(row.get("code") or "") if ch.isdigit()).zfill(6)
        org_id = str(row.get("orgId") or "").strip()
        if len(code) == 6 and org_id:
            mapping[code] = org_id
    if not mapping:
        raise ValueError("CNINFO stock map resolved no code/orgId pairs")
    return mapping


def _publication_text(value: object) -> str:
    if isinstance(value, (int, float)) and not pd.isna(value):
        ts = pd.to_datetime(int(value), unit="ms", utc=True).tz_convert("Asia/Shanghai")
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text:
        raise ValueError("CNINFO announcement lacks announcementTime")
    if text.isdigit() and len(text) >= 12:
        ts = pd.to_datetime(int(text), unit="ms", utc=True).tz_convert("Asia/Shanghai")
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return text


def _attachment_url(value: object) -> str:
    """Normalize an immutable CNINFO attachment path without inventing one.

    CNINFO's archive response normally supplies ``adjunctUrl`` for the exact
    published attachment.  The direct detail page remains the announcement
    identity, while this URL pins the bytes that a later filing parser may read.
    Missing attachments are allowed for non-document notices and remain blank.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("https://", "http://")):
        return text
    return f"{CNINFO_STATIC_ORIGIN}/{text.lstrip('/')}"


def fetch_cninfo_announcements_direct(
    *,
    symbol: str,
    market: str = "沪深京",
    keyword: str = "",
    category: str = "",
    start_date: object,
    end_date: object,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch the canonical CNINFO archive with explicit orgId and pagination.

    Besides the stable announcement id/detail page, preserve the exact official
    attachment URL returned by the archive.  Downstream PIT reconstruction must
    parse that versioned document rather than a mutable current-state F10 view.
    """

    del market  # CNINFO's fulltext endpoint is selected through the stock identity.
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    if len(code) != 6:
        raise ValueError(f"CNINFO symbol must resolve to six digits: {symbol}")
    org_id = _stock_org_map().get(code)
    if not org_id:
        raise ValueError(f"CNINFO stock map has no orgId for {code}; refusing inferred identity")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.cninfo.com.cn",
        "Referer": CNINFO_REFERER,
        "User-Agent": _USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    columns = ["代码", "简称", "公告标题", "公告时间", "公告链接", "公告附件链接"]
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        body = urlencode(
            {
                "pageNum": str(page),
                "pageSize": "30",
                "column": "szse",
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{code},{org_id}",
                "searchkey": keyword,
                "secid": "",
                "category": category,
                "trade": "",
                "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
        ).encode("utf-8")
        request = Request(CNINFO_QUERY_URL, data=body, headers=headers, method="POST")
        payload = _read_json(request, timeout)
        announcements = payload.get("announcements")
        if announcements is None:
            raise ValueError("CNINFO archive payload lacks announcements")
        if not isinstance(announcements, list):
            raise ValueError("CNINFO announcements must be a list")
        for item in announcements:
            if not isinstance(item, dict):
                continue
            sec_code = "".join(ch for ch in str(item.get("secCode") or code) if ch.isdigit()).zfill(6)
            if sec_code != code:
                continue
            announcement_id = str(item.get("announcementId") or "").strip()
            title = re.sub(r"<[^>]+>", "", str(item.get("announcementTitle") or "")).strip()
            when = _publication_text(item.get("announcementTime"))
            if not announcement_id or not title:
                raise ValueError("CNINFO announcement lacks immutable id/title")
            detail = (
                "https://www.cninfo.com.cn/new/disclosure/detail?"
                f"stockCode={code}&announcementId={announcement_id}&orgId={org_id}"
                f"&announcementTime={when[:10]}"
            )
            attachment = _attachment_url(
                item.get("adjunctUrl") or item.get("adjunctURL") or item.get("attachmentUrl")
            )
            rows.append(
                {
                    "代码": code,
                    "简称": str(item.get("secName") or "").strip(),
                    "公告标题": title,
                    "公告时间": when,
                    "公告链接": detail,
                    "公告附件链接": attachment,
                }
            )
        total_pages_raw = payload.get("totalpages") or payload.get("totalPages")
        if total_pages_raw is not None:
            try:
                total_pages = int(total_pages_raw)
            except (TypeError, ValueError):
                total_pages = page
            if page >= total_pages:
                break
        elif len(announcements) < 30:
            break
        page += 1
        if page > 2000:
            raise ValueError("CNINFO pagination exceeded defensive limit")
    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows, columns=columns)
        .drop_duplicates(subset=["代码", "公告链接"], keep="first")
        .reset_index(drop=True)
    )


__all__ = [
    "CNINFO_QUERY_URL",
    "CNINFO_STOCK_MAP_URL",
    "CNINFO_STATIC_ORIGIN",
    "fetch_cninfo_announcements_direct",
]
