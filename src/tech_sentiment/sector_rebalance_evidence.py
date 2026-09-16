from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Mapping, Sequence

import pandas as pd


CSINDEX_HOME = "https://www.csindex.com.cn/csindex-home"


@dataclass(frozen=True)
class RebalanceNotice:
    notice_id: int
    title: str
    publish_date: str
    detail_url: str


@dataclass(frozen=True)
class RebalanceAttachment:
    notice_id: int
    title: str
    publish_date: str
    effective_date: str
    file_name: str
    file_url: str


def announcement_payload(search_input: str, *, page: int = 1, rows: int = 80) -> dict[str, object]:
    if page < 1 or rows < 1:
        raise ValueError("page and rows must be positive")
    return {
        "lang": "cn",
        "searchInput": search_input,
        "page": {"key": "", "order": None, "page": page, "rows": rows, "sortBy": ""},
        "classList": [],
        "indexList": [],
        "relatedTopics": [],
        "typeList": [],
    }


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_notice_rows(
    payload: Mapping[str, object],
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[RebalanceNotice]:
    if str(payload.get("code", "")) != "200":
        raise ValueError(f"CSIndex announcement API code is not 200: {payload.get('code')!r}")
    raw = payload.get("data")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("CSIndex announcement payload missing data list")

    since_ts = pd.Timestamp(since).normalize() if since else None
    until_ts = pd.Timestamp(until).normalize() if until else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise ValueError("since must be <= until")

    out: dict[int, RebalanceNotice] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            notice_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        title = _clean(item.get("title"))
        publish_date = _clean(item.get("publishDate"))
        if not title or not publish_date:
            continue
        try:
            publish_ts = pd.Timestamp(publish_date).normalize()
        except ValueError:
            continue
        if since_ts is not None and publish_ts < since_ts:
            continue
        if until_ts is not None and publish_ts > until_ts:
            continue
        out[notice_id] = RebalanceNotice(
            notice_id=notice_id,
            title=title,
            publish_date=publish_ts.strftime("%Y-%m-%d"),
            detail_url=f"{CSINDEX_HOME}/announcement/queryAnnouncementById?id={notice_id}",
        )
    return sorted(out.values(), key=lambda row: (row.publish_date, row.notice_id))


def extract_effective_date(detail: Mapping[str, object]) -> str:
    html = _clean(detail.get("content"))
    text = re.sub(r"<[^>]+>", "", html)
    compact = re.sub(r"\s+", "", text)
    match = re.search(r"于?(\d{4})年(\d{1,2})月(\d{1,2})日[^。；;]{0,40}(?:生效|实施)", compact)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day).isoformat()


def extract_attachments(detail: Mapping[str, object]) -> list[RebalanceAttachment]:
    try:
        notice_id = int(detail.get("id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("notice detail missing integer id") from exc
    title = _clean(detail.get("title"))
    publish_date = _clean(detail.get("publishDate"))
    effective_date = extract_effective_date(detail)
    raw_list = detail.get("enclosureList") or []
    if not isinstance(raw_list, Sequence) or isinstance(raw_list, (str, bytes)):
        raise ValueError("enclosureList must be a list")

    out: list[RebalanceAttachment] = []
    for raw in raw_list:
        if not isinstance(raw, Mapping):
            continue
        file_url = _clean(raw.get("fileUrl"))
        file_name = _clean(raw.get("fileName") or raw.get("name"))
        if not file_url:
            continue
        out.append(
            RebalanceAttachment(
                notice_id=notice_id,
                title=title,
                publish_date=publish_date,
                effective_date=effective_date,
                file_name=file_name,
                file_url=file_url,
            )
        )
    return out


def _normalize_six_digit_code(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    exact = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if exact:
        return exact.group(1)
    if re.fullmatch(r"\d{1,6}(?:\.0+)?", text):
        integer_text = text.split(".", 1)[0]
        return integer_text.zfill(6)
    return ""


def _normalize_index_code(value: object) -> str:
    return _normalize_six_digit_code(value)


def _normalize_security_code(value: object) -> str:
    return _normalize_six_digit_code(value)


def extract_index_changes_from_sheets(
    sheets: Mapping[str, pd.DataFrame],
    *,
    index_code: str,
    effective_date: str,
) -> pd.DataFrame:
    """Extract one index's add/remove rows from official rebalance workbook sheets.

    The workbook remains the evidence object. This function only projects rows
    whose `指数代码` matches the requested index. It never infers a missing row,
    a no-change period, or a constituent from price data.
    """

    expected = {"调入": "add", "调出": "remove"}
    rows: list[dict[str, str]] = []
    wanted = _normalize_index_code(index_code)
    if not wanted:
        raise ValueError(f"invalid six-digit index code: {index_code!r}")
    for sheet_name, change_type in expected.items():
        frame = sheets.get(sheet_name)
        if frame is None or frame.empty:
            continue
        if "指数代码" not in frame.columns or "证券代码" not in frame.columns:
            continue
        for _, raw in frame.iterrows():
            if _normalize_index_code(raw.get("指数代码")) != wanted:
                continue
            security_code = _normalize_security_code(raw.get("证券代码"))
            if not security_code:
                continue
            rows.append(
                {
                    "index_code": wanted,
                    "effective_date": effective_date,
                    "change_type": change_type,
                    "security_code": security_code,
                    "security_name": _clean(raw.get("证券简称") or raw.get("证券名称")),
                    "evidence_status": "official_attachment_row",
                }
            )
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(
            columns=[
                "index_code",
                "effective_date",
                "change_type",
                "security_code",
                "security_name",
                "evidence_status",
            ]
        )
    return result.drop_duplicates(
        subset=["index_code", "effective_date", "change_type", "security_code"]
    ).sort_values(["effective_date", "change_type", "security_code"], ignore_index=True)


def has_index_rows(sheets: Mapping[str, pd.DataFrame], *, index_code: str) -> bool:
    wanted = _normalize_index_code(index_code)
    if not wanted:
        raise ValueError(f"invalid six-digit index code: {index_code!r}")
    for frame in sheets.values():
        if frame is None or frame.empty or "指数代码" not in frame.columns:
            continue
        if frame["指数代码"].map(_normalize_index_code).eq(wanted).any():
            return True
    return False
