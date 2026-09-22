from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import pandas as pd
import requests


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_QUERY_FALLBACK_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_MAP_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_STOCK_MAP_FALLBACK_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_TOPSEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_TOPSEARCH_FALLBACK_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_REFERER = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
CNINFO_STATIC_ORIGIN = "https://static.cninfo.com.cn"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": CNINFO_REFERER,
    "User-Agent": _USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
}
_ORG_ID_CACHE: dict[str, str] = {}
_STOCK_MAP_CACHE: dict[str, str] | None = None


class CninfoProtocolError(RuntimeError):
    """CNINFO public protocol failed validation or identity resolution."""


def _request_json(
    session: requests.Session,
    *,
    method: str,
    urls: tuple[str, ...],
    timeout: float,
    data: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> tuple[Any, str]:
    """Request one official CNINFO endpoint with bounded retry and HTTPS→HTTP fallback.

    Both URLs must be the same official cninfo.com.cn endpoint. The fallback is
    transport-only; evidence identity still comes from exact CNINFO document IDs
    and immutable attachment URLs.
    """

    errors: list[str] = []
    for url in urls:
        if not url.startswith(("https://www.cninfo.com.cn/", "http://www.cninfo.com.cn/")):
            raise ValueError(f"non-CNINFO endpoint refused: {url}")
        for attempt in range(1, max_attempts + 1):
            try:
                response = session.request(
                    method,
                    url,
                    headers=_DEFAULT_HEADERS,
                    data=data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json(), url
            except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    f"{url}|attempt={attempt}|{type(exc).__name__}:{str(exc)[:240]}"
                )
                if attempt < max_attempts:
                    time.sleep(min(0.5 * (2 ** (attempt - 1)), 2.0))
    raise CninfoProtocolError("CNINFO request failed: " + " || ".join(errors))


def _parse_topsearch_org_id(payload: object, code: str) -> str:
    if not isinstance(payload, list):
        raise CninfoProtocolError("CNINFO topSearch response must be a list")
    matches = [
        str(item.get("orgId") or "").strip()
        for item in payload
        if isinstance(item, dict)
        and str(item.get("code") or "").strip() == code
        and str(item.get("orgId") or "").strip()
    ]
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise CninfoProtocolError(
            f"CNINFO topSearch exact orgId resolution failed for {code}: matches={len(unique)}"
        )
    return unique[0]


def _parse_stock_map(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise CninfoProtocolError("CNINFO stock map response must be an object")
    rows = payload.get("stockList")
    if not isinstance(rows, list):
        raise CninfoProtocolError("CNINFO stock map lacks stockList")
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = "".join(ch for ch in str(row.get("code") or "") if ch.isdigit()).zfill(6)
        org_id = str(row.get("orgId") or "").strip()
        if len(code) == 6 and org_id:
            existing = mapping.get(code)
            if existing is not None and existing != org_id:
                raise CninfoProtocolError(f"CNINFO stock map has conflicting orgId for {code}")
            mapping[code] = org_id
    if not mapping:
        raise CninfoProtocolError("CNINFO stock map resolved no code/orgId pairs")
    return mapping


def resolve_cninfo_org_id(
    code: str,
    *,
    timeout: float = 15.0,
    session: requests.Session | None = None,
) -> str:
    """Resolve an exact CNINFO orgId without inferred/hard-coded identities.

    The live official topSearch endpoint is primary. The official stock-map JSON
    is a validated fallback only. A failure of both paths is fail-closed.
    """

    normalized = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)
    if len(normalized) != 6:
        raise ValueError(f"CNINFO symbol must resolve to six digits: {code}")
    cached = _ORG_ID_CACHE.get(normalized)
    if cached:
        return cached

    owned = session is None
    client = session or requests.Session()
    errors: list[str] = []
    try:
        try:
            payload, _ = _request_json(
                client,
                method="POST",
                urls=(CNINFO_TOPSEARCH_URL, CNINFO_TOPSEARCH_FALLBACK_URL),
                timeout=timeout,
                data={"keyWord": normalized, "maxNum": "10"},
            )
            org_id = _parse_topsearch_org_id(payload, normalized)
            _ORG_ID_CACHE[normalized] = org_id
            return org_id
        except Exception as exc:
            errors.append(f"topSearch:{type(exc).__name__}:{str(exc)[:300]}")

        global _STOCK_MAP_CACHE
        try:
            if _STOCK_MAP_CACHE is None:
                payload, _ = _request_json(
                    client,
                    method="GET",
                    urls=(CNINFO_STOCK_MAP_URL, CNINFO_STOCK_MAP_FALLBACK_URL),
                    timeout=timeout,
                )
                _STOCK_MAP_CACHE = _parse_stock_map(payload)
            org_id = str(_STOCK_MAP_CACHE.get(normalized) or "").strip()
            if not org_id:
                raise CninfoProtocolError(
                    f"CNINFO stock map has no exact orgId for {normalized}"
                )
            _ORG_ID_CACHE[normalized] = org_id
            return org_id
        except Exception as exc:
            errors.append(f"stockMap:{type(exc).__name__}:{str(exc)[:300]}")
    finally:
        if owned:
            client.close()

    raise CninfoProtocolError(
        f"CNINFO exact orgId resolution failed for {normalized}; " + " | ".join(errors)
    )


def _column_and_plate(code: str) -> tuple[str, str]:
    if code.startswith(("5", "6", "9")):
        return "sse", "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "szse", "sz"
    # Frozen V4-A scope contains no BJ symbols. Do not guess an undocumented
    # CNINFO column for a source/scope that is not currently required.
    raise CninfoProtocolError(f"CNINFO column mapping not frozen for symbol {code}")


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
    """Normalize an immutable CNINFO attachment path without inventing one."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("https://", "http://")):
        return text
    return f"{CNINFO_STATIC_ORIGIN}/{text.lstrip('/')}"


def _announcement_page(payload: object) -> tuple[list[dict[str, object]], bool]:
    if not isinstance(payload, dict):
        raise CninfoProtocolError("CNINFO announcement response must be an object")
    if "announcements" not in payload:
        raise CninfoProtocolError("CNINFO archive payload lacks announcements field")
    raw = payload.get("announcements")
    if raw is None:
        announcements: list[dict[str, object]] = []
    elif isinstance(raw, list):
        announcements = [item for item in raw if isinstance(item, dict)]
    else:
        raise CninfoProtocolError("CNINFO announcements field is not a list/null")

    has_more_raw = payload.get("hasMore")
    if isinstance(has_more_raw, bool):
        has_more = has_more_raw
    elif str(has_more_raw).strip().lower() in {"true", "1"}:
        has_more = True
    elif str(has_more_raw).strip().lower() in {"false", "0", "none", ""}:
        has_more = False
    else:
        has_more = False

    total_pages_raw = payload.get("totalpages")
    if total_pages_raw is None:
        total_pages_raw = payload.get("totalPages")
    if total_pages_raw is not None:
        try:
            total_pages = int(total_pages_raw)
        except (TypeError, ValueError):
            total_pages = 0
        if total_pages < 0:
            raise CninfoProtocolError("CNINFO total pages cannot be negative")
    return announcements, has_more


def fetch_cninfo_announcements_direct(
    *,
    symbol: str,
    market: str = "沪深京",
    keyword: str = "",
    category: str = "",
    start_date: object,
    end_date: object,
    timeout: float = 30.0,
    sleep_seconds: float = 0.15,
    session: requests.Session | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> pd.DataFrame:
    """Fetch CNINFO announcements with exact orgId, exchange column and pagination.

    This adapter is an acquisition implementation for the existing canonical
    CNINFO evidence source. It never infers orgId and never converts an endpoint
    failure into a successful empty historical window.
    """

    del market
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    if len(code) != 6:
        raise ValueError(f"CNINFO symbol must resolve to six digits: {symbol}")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    column, plate = _column_and_plate(code)

    owned = session is None
    client = session or requests.Session()
    columns = ["代码", "简称", "公告标题", "公告时间", "公告链接", "公告附件链接"]
    rows: list[dict[str, object]] = []
    try:
        org_id = resolve_cninfo_org_id(code, timeout=min(timeout, 15.0), session=client)
        page = 1
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "CNINFO_QUERY_START",
                    "symbol": code,
                    "start_date": str(start.date()),
                    "end_date": str(end.date()),
                }
            )
        while True:
            body = {
                "pageNum": str(page),
                "pageSize": "30",
                "column": column,
                "tabName": "fulltext",
                "plate": plate,
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
            payload, _ = _request_json(
                client,
                method="POST",
                urls=(CNINFO_QUERY_URL, CNINFO_QUERY_FALLBACK_URL),
                timeout=timeout,
                data=body,
            )
            announcements, has_more = _announcement_page(payload)
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "CNINFO_QUERY_PAGE",
                        "symbol": code,
                        "page": page,
                        "rows": len(announcements),
                        "accumulated_rows": len(rows),
                        "has_more": bool(has_more),
                    }
                )
            for item in announcements:
                sec_code = "".join(
                    ch for ch in str(item.get("secCode") or code) if ch.isdigit()
                ).zfill(6)
                if sec_code != code:
                    continue
                announcement_id = str(item.get("announcementId") or "").strip()
                title = re.sub(
                    r"<[^>]+>", "", str(item.get("announcementTitle") or "")
                ).strip()
                when = _publication_text(item.get("announcementTime"))
                if not announcement_id or not title:
                    raise CninfoProtocolError(
                        "CNINFO announcement lacks immutable id/title"
                    )
                detail = (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    f"stockCode={code}&announcementId={announcement_id}&orgId={org_id}"
                    f"&announcementTime={when[:10]}"
                )
                attachment = _attachment_url(
                    item.get("adjunctUrl")
                    or item.get("adjunctURL")
                    or item.get("attachmentUrl")
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

            if not has_more:
                # totalpages is inconsistent across CNINFO deployments; a short
                # page is also a safe terminal condition.
                total_pages_raw = (
                    payload.get("totalpages") if isinstance(payload, dict) else None
                )
                if total_pages_raw is None and isinstance(payload, dict):
                    total_pages_raw = payload.get("totalPages")
                if total_pages_raw is None:
                    break
                try:
                    if page >= int(total_pages_raw):
                        break
                except (TypeError, ValueError):
                    if len(announcements) < 30:
                        break
            if not announcements:
                if has_more:
                    raise CninfoProtocolError(
                        "CNINFO pagination reports hasMore with an empty page"
                    )
                break
            page += 1
            if page > 2000:
                raise CninfoProtocolError("CNINFO pagination exceeded defensive limit")
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    finally:
        if owned:
            client.close()

    if progress_callback is not None:
        progress_callback(
            {
                "event": "CNINFO_QUERY_COMPLETE",
                "symbol": code,
                "pages": page,
                "rows": len(rows),
            }
        )
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
    "CNINFO_TOPSEARCH_URL",
    "CNINFO_STATIC_ORIGIN",
    "CninfoProtocolError",
    "resolve_cninfo_org_id",
    "fetch_cninfo_announcements_direct",
]
