from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Iterable, Mapping
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import pandas as pd


CSINDEX_HOME = "https://www.csindex.com.cn/csindex-home"
CSINDEX_SITE = "https://www.csindex.com.cn"
CSI_931152 = "931152"
CSI_931152_NAME = "中证创新药产业指数"
DESIGN_START = pd.Timestamp("2019-04-22")
DESIGN_END = pd.Timestamp("2023-12-31")
_ALLOWED_ATTACHMENT_SUFFIXES = {".xls", ".xlsx", ".csv", ".zip", ".pdf"}

DEFAULT_SEARCH_TERMS = (
    "中证创新药产业指数",
    "CS创新药",
    "931152",
    "指数定期调整结果",
)


@dataclass(frozen=True)
class AnnouncementRef:
    notice_id: int
    title: str
    publish_date: str
    theme: str
    detail_url: str
    search_term: str


@dataclass(frozen=True)
class AttachmentRef:
    notice_id: int
    title: str
    publish_date: str
    effective_date: str
    file_name: str
    file_url: str
    detail_url: str


@dataclass(frozen=True)
class AdjustmentRow:
    notice_id: int
    publish_date: str
    effective_date: str
    action: str
    symbol: str
    attachment_url: str
    attachment_sha256: str


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://www.csindex.com.cn/zh-CN/about/newsCenter",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
        ),
    }


def announcement_payload(search_input: str, *, page: int = 1, rows: int = 100) -> dict[str, object]:
    if page < 1:
        raise ValueError("page must be >= 1")
    if rows < 1:
        raise ValueError("rows must be >= 1")
    return {
        "lang": "cn",
        "searchInput": search_input,
        "page": {"key": "", "order": None, "page": page, "rows": rows, "sortBy": ""},
        "classList": [],
        "indexList": [],
        "relatedTopics": [],
        "typeList": [],
    }


def _json_request(url: str, *, payload: Mapping[str, object] | None = None, timeout: int = 30) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=_headers(), method="POST" if body is not None else "GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official host
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("CSIndex response is not a JSON object")
    if str(parsed.get("code", "")) not in {"200", "0", ""}:
        raise ValueError(f"CSIndex API code is not success: {parsed.get('code')!r}")
    return parsed


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _date_in_design_window(value: object) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    try:
        ts = pd.Timestamp(text).normalize()
    except (TypeError, ValueError):
        return False
    return DESIGN_START <= ts <= DESIGN_END


def parse_announcement_search(
    payload: Mapping[str, object],
    *,
    search_term: str,
) -> list[AnnouncementRef]:
    raw = payload.get("data")
    if not isinstance(raw, list):
        return []
    out: list[AnnouncementRef] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        try:
            notice_id = int(str(row.get("id", "")).strip())
        except ValueError:
            continue
        publish_date = _clean_text(row.get("publishDate"))
        if not _date_in_design_window(publish_date):
            continue
        title = _clean_text(row.get("title"))
        theme = _clean_text(row.get("theme"))
        if theme and theme != "指数调样":
            continue
        out.append(
            AnnouncementRef(
                notice_id=notice_id,
                title=title,
                publish_date=publish_date,
                theme=theme,
                detail_url=f"{CSINDEX_SITE}/zh-CN/about/newsDetail?id={notice_id}",
                search_term=search_term,
            )
        )
    return out


def query_design_announcements(
    *,
    search_terms: Iterable[str] = DEFAULT_SEARCH_TERMS,
    rows: int = 100,
    timeout: int = 30,
) -> list[AnnouncementRef]:
    dedup: dict[int, AnnouncementRef] = {}
    endpoint = f"{CSINDEX_HOME}/announcement/queryAnnouncementByVo"
    for term in search_terms:
        payload = _json_request(
            endpoint,
            payload=announcement_payload(str(term), rows=rows),
            timeout=timeout,
        )
        for notice in parse_announcement_search(payload, search_term=str(term)):
            dedup.setdefault(notice.notice_id, notice)
    return sorted(dedup.values(), key=lambda item: (item.publish_date, item.notice_id))


def fetch_announcement_detail(notice_id: int, *, timeout: int = 30) -> dict[str, object]:
    if notice_id <= 0:
        raise ValueError("notice_id must be positive")
    query = urlencode({"id": notice_id})
    payload = _json_request(
        f"{CSINDEX_HOME}/announcement/queryAnnouncementById?{query}",
        timeout=timeout,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"CSIndex notice detail missing for id={notice_id}")
    return data


def extract_effective_date(content_html: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(content_html or ""))
    compact = re.sub(r"\s+", "", text)
    patterns = (
        r"于(\d{4})年(\d{1,2})月(\d{1,2})日[^。；;]{0,40}(?:生效|实施)",
        r"(\d{4})年(\d{1,2})月(\d{1,2})日[^。；;]{0,40}(?:正式实施|正式生效)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            year, month, day = (int(value) for value in match.groups())
            return date(year, month, day).isoformat()
    return ""


def _is_official_csindex_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "csindex.com.cn" or host.endswith(".csindex.com.cn")


def canonicalize_attachment_url(raw_url: object) -> str:
    text = _clean_text(raw_url)
    if not text:
        return ""
    joined = urljoin(CSINDEX_SITE, text)
    parts = urlsplit(joined)
    if not _is_official_csindex_host(parts.hostname):
        return ""
    scheme = "https" if parts.scheme in {"", "http", "https"} else parts.scheme
    if scheme != "https":
        return ""
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def _downloadable_content_link(url: str) -> bool:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix in _ALLOWED_ATTACHMENT_SUFFIXES


def attachment_refs_from_detail(detail: Mapping[str, object]) -> list[AttachmentRef]:
    try:
        notice_id = int(str(detail.get("id", "")).strip())
    except ValueError:
        return []
    title = _clean_text(detail.get("title"))
    publish_date = _clean_text(detail.get("publishDate"))
    content = str(detail.get("content") or "")
    effective_date = extract_effective_date(content)
    detail_url = f"{CSINDEX_SITE}/zh-CN/about/newsDetail?id={notice_id}"
    out: list[AttachmentRef] = []
    seen: set[str] = set()

    raw_enclosures = detail.get("enclosureList")
    enclosures = raw_enclosures if isinstance(raw_enclosures, list) else []
    for item in enclosures:
        if not isinstance(item, Mapping):
            continue
        file_url = canonicalize_attachment_url(item.get("fileUrl") or item.get("url"))
        if not file_url or file_url in seen:
            continue
        seen.add(file_url)
        file_name = _clean_text(item.get("fileName") or item.get("name"))
        if not file_name:
            file_name = Path(urlsplit(file_url).path).name
        out.append(
            AttachmentRef(
                notice_id=notice_id,
                title=title,
                publish_date=publish_date,
                effective_date=effective_date,
                file_name=file_name,
                file_url=file_url,
                detail_url=detail_url,
            )
        )

    hrefs = re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", content, flags=re.IGNORECASE)
    for href in hrefs:
        file_url = canonicalize_attachment_url(href)
        if not file_url or file_url in seen or not _downloadable_content_link(file_url):
            continue
        seen.add(file_url)
        out.append(
            AttachmentRef(
                notice_id=notice_id,
                title=title,
                publish_date=publish_date,
                effective_date=effective_date,
                file_name=Path(urlsplit(file_url).path).name,
                file_url=file_url,
                detail_url=detail_url,
            )
        )
    return out


def _request_safe_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not _is_official_csindex_host(parts.hostname):
        raise ValueError("attachment URL must be an official CSIndex https URL")
    safe_path = quote(parts.path, safe="/%:@-._~!$&'()*+,;=")
    safe_query = quote(parts.query, safe="=&%:@-._~!$'()*+,;/?")
    return urlunsplit((parts.scheme, parts.netloc, safe_path, safe_query, parts.fragment))


def download_attachment(url: str, *, timeout: int = 30) -> tuple[bytes, str]:
    canonical = canonicalize_attachment_url(url)
    if not canonical:
        raise ValueError("attachment URL must be an official CSIndex URL")
    request = Request(_request_safe_url(canonical), headers=_headers(), method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - host restricted above
        raw = response.read()
    if not raw:
        raise ValueError("empty adjustment attachment")
    return raw, sha256(raw).hexdigest()


def _find_header_row(frame: pd.DataFrame) -> int | None:
    for idx, row in frame.iterrows():
        values = {_clean_text(value) for value in row.tolist()}
        if "指数代码" in values and "证券代码" in values:
            return int(idx)
    return None


def _normalise_code(value: object) -> str:
    text = _clean_text(value)
    if not text or text.lower() == "nan":
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        text = text.split(".", 1)[0]
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def extract_index_changes_from_sheets(
    sheets: Mapping[str, pd.DataFrame],
    *,
    index_code: str = CSI_931152,
) -> dict[str, list[str]]:
    result = {"add": [], "remove": []}
    aliases = {"调入": "add", "调出": "remove"}
    for sheet_name, action in aliases.items():
        if sheet_name not in sheets:
            continue
        raw = sheets[sheet_name].copy()
        header_row = _find_header_row(raw)
        if header_row is None:
            continue
        headers = [_clean_text(value) for value in raw.iloc[header_row].tolist()]
        body = raw.iloc[header_row + 1 :].copy()
        body.columns = headers
        if "指数代码" not in body.columns or "证券代码" not in body.columns:
            continue
        idx = body["指数代码"].map(_normalise_code)
        selected = body.loc[idx.eq(_normalise_code(index_code)), "证券代码"]
        symbols = sorted({code for code in selected.map(_normalise_code) if code})
        result[action] = symbols
    return result


def parse_adjustment_workbook(
    raw: bytes,
    *,
    index_code: str = CSI_931152,
) -> dict[str, list[str]]:
    try:
        sheets = pd.read_excel(BytesIO(raw), sheet_name=None, header=None)
    except ImportError as exc:
        raise RuntimeError(
            "Excel parser missing; install the research-only sector-data extra"
        ) from exc
    if not isinstance(sheets, dict):
        raise ValueError("adjustment workbook did not produce sheet mapping")
    return extract_index_changes_from_sheets(sheets, index_code=index_code)


def build_adjustment_rows(
    attachment: AttachmentRef,
    *,
    workbook_bytes: bytes,
    index_code: str = CSI_931152,
) -> list[AdjustmentRow]:
    changes = parse_adjustment_workbook(workbook_bytes, index_code=index_code)
    digest = sha256(workbook_bytes).hexdigest()
    out: list[AdjustmentRow] = []
    for action in ("add", "remove"):
        for symbol in changes[action]:
            out.append(
                AdjustmentRow(
                    notice_id=attachment.notice_id,
                    publish_date=attachment.publish_date,
                    effective_date=attachment.effective_date,
                    action=action,
                    symbol=symbol,
                    attachment_url=attachment.file_url,
                    attachment_sha256=digest,
                )
            )
    return out
