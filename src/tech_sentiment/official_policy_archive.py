from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import pandas as pd

from .pit_public_materialization import (
    PitMaterializationResult,
    _market_available_date,
    _real_trading_calendar,
    _stable_hash,
    validate_materialized_pit_records,
)


POLICY_SOURCE_ID = "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"
POLICY_PROVIDER = "CSRC"
POLICY_ENTITY_ID = "MARKET.CN"

# Fixed official categories only. Search-engine results are never canonical input.
CSRC_LISTS: dict[str, tuple[str, str]] = {
    "CSRC_ORDERS": (
        "https://www.csrc.gov.cn/csrc/c101953/common_list.shtml",
        "REGULATORY_EVENT",
    ),
    "CSRC_ANNOUNCEMENTS": (
        "https://www.csrc.gov.cn/csrc/c101954/common_list.shtml",
        "REGULATORY_EVENT",
    ),
    "CSRC_DAILY_REGULATORY": (
        "https://www.csrc.gov.cn/csrc/c100040/common_list.shtml",
        "REGULATORY_EVENT",
    ),
    "CSRC_POLICY_INTERPRETATION": (
        "https://www.csrc.gov.cn/csrc/c100039/common_list.shtml",
        "POLICY_EVENT",
    ),
}

_NEGATIVE_TOKENS = (
    "行政处罚",
    "市场禁入",
    "立案",
    "处罚决定",
    "监管措施",
    "责令改正",
    "暂停业务",
    "撤销许可",
)


@dataclass(frozen=True)
class PolicyListEntry:
    segment: str
    title: str
    publication_date: pd.Timestamp
    url: str
    default_evidence_type: str


def _fetch_text(url: str, timeout: float = 30.0) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.csrc.gov.cn":
        raise ValueError("policy archive URL must be on the official CSRC HTTPS host")
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed official host
        raw = response.read()
    if not raw:
        raise ValueError("CSRC archive returned an empty page")
    return raw.decode("utf-8", errors="replace")


def _page_url(base_url: str, page: int) -> str:
    if page == 0:
        return base_url
    if not base_url.endswith(".shtml"):
        raise ValueError("unsupported CSRC list URL shape")
    return f"{base_url[:-6]}_{page}.shtml"


def _parse_date(value: str) -> pd.Timestamp | None:
    match = re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?", value)
    if not match:
        return None
    return pd.Timestamp(
        year=int(match.group(1)), month=int(match.group(2)), day=int(match.group(3))
    )


def parse_csrc_list_page(
    html: str,
    *,
    page_url: str,
    segment: str,
    default_evidence_type: str,
) -> tuple[list[PolicyListEntry], int]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 is required for CSRC archive parsing") from exc

    soup = BeautifulSoup(html, "html.parser")
    entries: list[PolicyListEntry] = []
    undated_links = 0
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        url = urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(url)
        if parsed.hostname != "www.csrc.gov.cn" or not parsed.path.endswith("content.shtml"):
            continue
        if url in seen:
            continue
        seen.add(url)

        context_parts = [anchor.get_text(" ", strip=True)]
        parent = anchor.parent
        if parent is not None:
            context_parts.append(parent.get_text(" ", strip=True))
        nxt = anchor.find_next(string=re.compile(r"20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}"))
        if nxt:
            context_parts.append(str(nxt))
        date = _parse_date(" ".join(context_parts))
        if date is None:
            undated_links += 1
            continue
        entries.append(
            PolicyListEntry(
                segment=segment,
                title=title,
                publication_date=date.normalize(),
                url=url,
                default_evidence_type=default_evidence_type,
            )
        )
    return entries, undated_links


def _document_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[-1] == "content.shtml":
        return parts[-2]
    return sha256(url.encode("utf-8")).hexdigest()[:24]


def _evidence_type(entry: PolicyListEntry) -> str:
    if any(token in entry.title for token in _NEGATIVE_TOKENS):
        return "MAJOR_NEGATIVE_EVENT"
    return entry.default_evidence_type


def materialize_csrc_policy_archive(
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    fetcher: Callable[[str], str] = _fetch_text,
    max_pages_per_segment: int = 500,
) -> PitMaterializationResult:
    """Materialize fixed CSRC lists with explicit full-window pagination audit."""

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if max_pages_per_segment <= 0:
        raise ValueError("max_pages_per_segment must be positive")
    calendar = _real_trading_calendar(trading_dates)
    captured = datetime.now(timezone.utc).isoformat()

    all_entries: dict[str, PolicyListEntry] = {}
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for segment, (base_url, default_type) in CSRC_LISTS.items():
        reached_start = False
        pages_read = 0
        segment_failed = False
        for page in range(max_pages_per_segment):
            url = _page_url(base_url, page)
            try:
                html = fetcher(url)
                entries, undated = parse_csrc_list_page(
                    html,
                    page_url=url,
                    segment=segment,
                    default_evidence_type=default_type,
                )
            except Exception as exc:
                errors.append(
                    {
                        "source_identity": POLICY_SOURCE_ID,
                        "entity_id": POLICY_ENTITY_ID,
                        "error": f"{segment}:{url}:{type(exc).__name__}: {exc}",
                    }
                )
                segment_failed = True
                break
            pages_read += 1
            if undated:
                errors.append(
                    {
                        "source_identity": POLICY_SOURCE_ID,
                        "entity_id": POLICY_ENTITY_ID,
                        "error": f"{segment}:{url}:UNDATED_CANONICAL_LINKS:{undated}",
                    }
                )
                segment_failed = True
                break
            if not entries:
                reached_start = True
                break
            for entry in entries:
                if start <= entry.publication_date <= end:
                    all_entries[entry.url] = entry
            oldest = min(entry.publication_date for entry in entries)
            if oldest <= start:
                reached_start = True
                break
        if not reached_start and not segment_failed:
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{segment}:PAGINATION_LIMIT_BEFORE_START_DATE",
                }
            )
            segment_failed = True
        coverage_rows.append(
            {
                "source_identity": POLICY_SOURCE_ID,
                "entity_id": POLICY_ENTITY_ID,
                "coverage_segment": segment,
                "coverage_start": start,
                "coverage_end": end,
                "query_status": "FAILED" if segment_failed else "COMPLETE_WINDOW",
                "pages_read": pages_read,
                "captured_at_utc": captured,
            }
        )

    evidence_rows: list[dict[str, object]] = []
    for entry in sorted(all_entries.values(), key=lambda item: (item.publication_date, item.url)):
        try:
            article = fetcher(entry.url)
            article_sha = sha256(article.encode("utf-8")).hexdigest()
            available_date, availability_rule = _market_available_date(
                str(entry.publication_date.date()), trading_dates=calendar
            )
        except Exception as exc:
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{entry.segment}:{entry.url}:{type(exc).__name__}: {exc}",
                }
            )
            continue
        doc_id = _document_id(entry.url)
        evidence_type = _evidence_type(entry)
        provenance = {
            "source_identity": POLICY_SOURCE_ID,
            "provider": POLICY_PROVIDER,
            "coverage_segment": entry.segment,
            "document_url": entry.url,
            "document_sha256": article_sha,
            "publication_date_source": "OFFICIAL_CSRC_LIST",
            "evidence_available_date_semantics": "NEXT_REAL_TRADING_DATE_FOR_DATE_ONLY_PUBLICATION",
            "availability_rule": availability_rule,
            "search_results_are_not_canonical_evidence": True,
        }
        identity = {
            "entity_id": POLICY_ENTITY_ID,
            "document_id": doc_id,
            "document_sha256": article_sha,
            "event_date": str(entry.publication_date.date()),
            "available": str(available_date.date()),
            "evidence_type": evidence_type,
        }
        evidence_rows.append(
            {
                "evidence_id": f"csrc:{doc_id}:{article_sha[:16]}",
                "entity_id": POLICY_ENTITY_ID,
                "evidence_type": evidence_type,
                "event_date": entry.publication_date,
                "evidence_available_date": available_date,
                "source_identity": POLICY_SOURCE_ID,
                "provider": POLICY_PROVIDER,
                "document_id": doc_id,
                "revision_id": f"DOCUMENT_SHA256:{article_sha}",
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(identity),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "title": entry.title,
                "source_url_identity": entry.url,
                "captured_at_utc": captured,
            }
        )

    records = (
        validate_materialized_pit_records(pd.DataFrame(evidence_rows))
        if evidence_rows
        else pd.DataFrame()
    )
    coverage = pd.DataFrame(coverage_rows)
    complete = bool(len(coverage)) and bool(
        coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all()
    )
    summary = {
        "source_identity": POLICY_SOURCE_ID,
        "provider": POLICY_PROVIDER,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "coverage_segments": sorted(CSRC_LISTS),
        "source_coverage_complete": complete,
        "materialized_records": int(len(records)),
        "error_rows": int(len(errors)),
        "readiness_state": (
            "QUALIFIED_INPUT" if complete and len(records) else "PARTIAL_COVERAGE" if len(records) else "DATA_INSUFFICIENT"
        ),
        "captured_at_utc": captured,
        "future_prices_or_returns_used": False,
        "search_results_are_not_canonical_evidence": True,
    }
    return PitMaterializationResult(
        records=records,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["source_identity", "entity_id", "error"]),
        summary=summary,
    )


__all__ = [
    "POLICY_SOURCE_ID",
    "POLICY_PROVIDER",
    "POLICY_ENTITY_ID",
    "CSRC_LISTS",
    "PolicyListEntry",
    "parse_csrc_list_page",
    "materialize_csrc_policy_archive",
]
