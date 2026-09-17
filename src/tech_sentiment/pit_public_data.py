from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Callable, Iterable

import pandas as pd


CNINFO_SOURCE_ID = "CNINFO_ANNOUNCEMENT_ARCHIVE"
CNINFO_PROVIDER = "CNINFO"
CNINFO_ARCHIVE_URL = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"


@dataclass(frozen=True)
class PitFetchResult:
    data: pd.DataFrame
    errors: pd.DataFrame


def _pick_column(frame: pd.DataFrame, candidates: Iterable[str], label: str) -> str:
    for column in candidates:
        if column in frame.columns:
            return column
    raise ValueError(f"CNINFO announcement response missing {label}: {list(candidates)}")


def _has_clock(value: object) -> bool:
    return bool(re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", str(value)))


def _next_trading_date(
    publication: pd.Timestamp,
    *,
    has_clock: bool,
    trading_dates: pd.DatetimeIndex,
) -> pd.Timestamp:
    day = publication.normalize()
    is_trading_day = day in trading_dates
    before_or_at_close = has_clock and publication.hour < 15 or (
        has_clock and publication.hour == 15 and publication.minute == 0 and publication.second == 0
    )
    if is_trading_day and before_or_at_close:
        return day
    later = trading_dates[trading_dates > day]
    if not len(later):
        raise ValueError(
            f"real trading calendar does not contain a post-publication market date for {publication}"
        )
    return pd.Timestamp(later[0]).normalize()


def _evidence_id(entity_id: str, document_id: str, revision_id: str) -> str:
    raw = f"{CNINFO_SOURCE_ID}|{entity_id}|{document_id}|{revision_id}".encode("utf-8")
    return sha256(raw).hexdigest()


def normalize_cninfo_announcements(
    frame: pd.DataFrame,
    *,
    entity_id: str,
    trading_dates: Iterable[object],
    ingestion_timestamp: object,
    repository_sha: str,
) -> pd.DataFrame:
    """Normalize raw CNINFO announcement metadata into an immutable PIT ledger.

    Daily research is close-based. When CNINFO exposes a precise publication
    clock at or before 15:00 on a real trading day, that day is the first usable
    market date. Date-only disclosures and after-close/non-trading-day
    disclosures are conservatively delayed to the next real trading date.

    This layer never infers event direction, fundamentals quality, earnings
    surprise, or price outcome from announcement titles.
    """

    if frame.empty:
        return pd.DataFrame(
            columns=[
                "evidence_id",
                "entity_id",
                "evidence_type",
                "event_date",
                "evidence_available_date",
                "source_identity",
                "provider",
                "document_id",
                "revision_id",
                "provenance",
                "ingestion_identity",
                "availability_state",
                "announcement_title",
                "publication_timestamp",
                "publication_precision",
                "source_url_identity",
            ]
        )

    title_col = _pick_column(frame, ("公告标题", "标题", "announcementTitle"), "title")
    time_col = _pick_column(frame, ("公告时间", "公告日期", "announcementTime"), "publication time")
    doc_col = _pick_column(frame, ("announcementId", "公告ID", "id"), "document id")
    code_col = next((column for column in ("代码", "证券代码", "secCode") if column in frame.columns), None)
    org_col = next((column for column in ("orgId", "组织机构代码") if column in frame.columns), None)
    url_col = next((column for column in ("adjunctUrl", "公告链接", "url") if column in frame.columns), None)

    calendar = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(calendar):
        raise ValueError("real trading calendar cannot be empty")
    ingest = pd.Timestamp(ingestion_timestamp)
    if ingest.tzinfo is None:
        ingest = ingest.tz_localize("UTC")
    else:
        ingest = ingest.tz_convert("UTC")
    repo_sha = str(repository_sha).strip()
    if not repo_sha:
        raise ValueError("repository_sha is required")

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for _, raw in frame.iterrows():
        title = str(raw[title_col]).strip()
        document_id = str(raw[doc_col]).strip()
        if not title or not document_id or document_id.lower() == "nan":
            raise ValueError("CNINFO announcement requires title and immutable document id")
        revision_id = document_id
        identity = (document_id, revision_id)
        if identity in seen:
            continue
        seen.add(identity)

        raw_time = raw[time_col]
        publication = pd.to_datetime(raw_time, errors="raise")
        if isinstance(publication, pd.DatetimeIndex):
            raise ValueError("unexpected vector publication timestamp")
        publication = pd.Timestamp(publication)
        clock = _has_clock(raw_time)
        available = _next_trading_date(
            publication,
            has_clock=clock,
            trading_dates=calendar,
        )
        if available < publication.normalize():
            raise ValueError("evidence_available_date cannot precede publication date")

        source_url_identity = (
            str(raw[url_col]).strip()
            if url_col is not None and pd.notna(raw[url_col]) and str(raw[url_col]).strip()
            else f"CNINFO_ANNOUNCEMENT_ID:{document_id}"
        )
        provenance = {
            "upstream_archive": CNINFO_ARCHIVE_URL,
            "provider_interface": "akshare.stock_zh_a_disclosure_report_cninfo",
            "repository_sha": repo_sha,
            "raw_document_id": document_id,
            "raw_org_id": (
                str(raw[org_col]).strip()
                if org_col is not None and pd.notna(raw[org_col])
                else None
            ),
            "raw_security_code": (
                str(raw[code_col]).strip()
                if code_col is not None and pd.notna(raw[code_col])
                else None
            ),
            "availability_rule": "same-trading-date-if-precise-at-or-before-15:00-else-next-real-trading-date",
        }
        rows.append(
            {
                "evidence_id": _evidence_id(str(entity_id), document_id, revision_id),
                "entity_id": str(entity_id),
                "evidence_type": "ISSUER_ANNOUNCEMENT",
                "event_date": publication.normalize(),
                "evidence_available_date": available,
                "source_identity": CNINFO_SOURCE_ID,
                "provider": CNINFO_PROVIDER,
                "document_id": document_id,
                "revision_id": revision_id,
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": f"{repo_sha}:{ingest.isoformat()}",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "announcement_title": title,
                "publication_timestamp": publication,
                "publication_precision": "TIMESTAMP" if clock else "DATE_ONLY_CONSERVATIVE_NEXT_TRADE_DATE",
                "source_url_identity": source_url_identity,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return normalize_cninfo_announcements(
            pd.DataFrame(),
            entity_id=entity_id,
            trading_dates=calendar,
            ingestion_timestamp=ingest,
            repository_sha=repo_sha,
        )
    if out["evidence_id"].duplicated().any():
        raise ValueError("CNINFO PIT evidence contains duplicate evidence_id")
    if (pd.to_datetime(out["evidence_available_date"]) < pd.to_datetime(out["event_date"])).any():
        raise ValueError("PIT evidence availability precedes event/publication date")
    return out.sort_values(
        ["evidence_available_date", "event_date", "document_id"]
    ).reset_index(drop=True)


def fetch_cninfo_announcement_history(
    *,
    entity_ids: Iterable[str],
    start_date: str,
    end_date: str,
    trading_dates: Iterable[object],
    ingestion_timestamp: object,
    repository_sha: str,
    fetcher: Callable[[str, str, str], pd.DataFrame] | None = None,
) -> PitFetchResult:
    if fetcher is None:
        import akshare as ak  # type: ignore

        def fetcher(symbol: str, start: str, end: str) -> pd.DataFrame:
            return ak.stock_zh_a_disclosure_report_cninfo(
                symbol=symbol,
                market="沪深京",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )

    parts: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    for entity in sorted({str(value).strip() for value in entity_ids if str(value).strip()}):
        symbol = entity.split(".", 1)[0]
        try:
            raw = fetcher(symbol, start_date, end_date)
            normalized = normalize_cninfo_announcements(
                raw,
                entity_id=entity,
                trading_dates=trading_dates,
                ingestion_timestamp=ingestion_timestamp,
                repository_sha=repository_sha,
            )
            if len(normalized):
                parts.append(normalized)
        except Exception as exc:
            errors.append(
                {
                    "entity_id": entity,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    data = pd.concat(parts, ignore_index=True) if parts else normalize_cninfo_announcements(
        pd.DataFrame(),
        entity_id="",
        trading_dates=trading_dates,
        ingestion_timestamp=ingestion_timestamp,
        repository_sha=repository_sha,
    )
    return PitFetchResult(
        data=data,
        errors=pd.DataFrame(errors, columns=["entity_id", "error"]),
    )


__all__ = [
    "CNINFO_SOURCE_ID",
    "CNINFO_PROVIDER",
    "CNINFO_ARCHIVE_URL",
    "PitFetchResult",
    "normalize_cninfo_announcements",
    "fetch_cninfo_announcement_history",
]
