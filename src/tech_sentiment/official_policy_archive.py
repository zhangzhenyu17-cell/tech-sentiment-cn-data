from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

import pandas as pd

from .bounded_retry import call_with_bounded_network_retry
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
CSRC_OFFICIAL_ORIGIN = "https://www.csrc.gov.cn"

# Fixed official categories only. Web/search-engine results are never canonical
# evidence. The formal producer resolves these channel codes through CSRC's own
# getLocalList endpoint and enumerates them through searchList/<channelId>.
CSRC_CHANNELS: dict[str, tuple[str, str]] = {
    "CSRC_ORDERS": ("c101953", "REGULATORY_EVENT"),
    "CSRC_ANNOUNCEMENTS": ("c101954", "REGULATORY_EVENT"),
    "CSRC_DAILY_REGULATORY": ("c100040", "REGULATORY_EVENT"),
    "CSRC_POLICY_INTERPRETATION": ("c100039", "POLICY_EVENT"),
}

# Retained as a public compatibility surface for documentation/tests. Formal
# materialization does not scrape these HTML pages.
CSRC_LISTS: dict[str, tuple[str, str]] = {
    segment: (
        f"{CSRC_OFFICIAL_ORIGIN}/csrc/{channel_code}/common_list.shtml",
        default_type,
    )
    for segment, (channel_code, default_type) in CSRC_CHANNELS.items()
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
class PolicyChannel:
    segment: str
    channel_code: str
    channel_id: str
    channel_name: str
    default_evidence_type: str


@dataclass(frozen=True)
class PolicyListEntry:
    segment: str
    title: str
    publication_date: pd.Timestamp
    url: str
    default_evidence_type: str
    manuscript_id: str = ""
    publication_timestamp: str = ""
    channel_code: str = ""
    channel_id: str = ""


def _validate_csrc_https_url(url: str) -> str:
    text = str(url or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    elif text.startswith("/"):
        text = urljoin(CSRC_OFFICIAL_ORIGIN + "/", text)
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.hostname != "www.csrc.gov.cn":
        raise ValueError("CSRC URL must be on the official HTTPS host")
    return text


def _validate_csrc_response_url(url: object) -> str:
    return _validate_csrc_https_url(str(url or ""))


def _csrc_browser_bytes(
    canonical: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    browser_get: Callable[..., object] | None = None,
) -> bytes:
    if browser_get is None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover - installed by the data extra
            raise RuntimeError(
                "curl_cffi is required for CSRC browser transport fallback"
            ) from exc
        browser_get = curl_requests.get

    response = browser_get(
        canonical,
        headers=dict(headers),
        impersonate="chrome",
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    _validate_csrc_response_url(getattr(response, "url", canonical))
    raw = bytes(getattr(response, "content", b""))
    if not raw:
        raise ValueError("CSRC browser transport returned an empty response")
    return raw


def _fetch_text(
    url: str,
    timeout: float = 30.0,
    *,
    opener: Callable[..., object] = urlopen,
    browser_get: Callable[..., object] | None = None,
) -> str:
    canonical = _validate_csrc_https_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": CSRC_OFFICIAL_ORIGIN + "/",
    }
    request = Request(canonical, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout) as response:  # nosec B310 - official HTTPS host checked above
            final_url = (
                response.geturl()
                if hasattr(response, "geturl")
                else canonical
            )
            _validate_csrc_response_url(final_url)
            raw = response.read()
        if not raw:
            raise ValueError("CSRC archive returned an empty page")
    except Exception:
        raw = _csrc_browser_bytes(
            canonical,
            headers=headers,
            timeout=timeout,
            browser_get=browser_get,
        )
    return raw.decode("utf-8", errors="replace")


def _fetch_json(
    url: str,
    timeout: float = 30.0,
    *,
    opener: Callable[..., object] = urlopen,
    browser_get: Callable[..., object] | None = None,
) -> dict[str, object]:
    canonical = _validate_csrc_https_url(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": CSRC_OFFICIAL_ORIGIN + "/",
    }
    request = Request(canonical, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout) as response:  # nosec B310 - official HTTPS host checked above
            final_url = (
                response.geturl()
                if hasattr(response, "geturl")
                else canonical
            )
            _validate_csrc_response_url(final_url)
            raw = response.read()
        if not raw:
            raise ValueError("CSRC JSON endpoint returned an empty response")
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(payload, dict):
            raise ValueError("CSRC JSON endpoint must return an object")
    except Exception:
        raw = _csrc_browser_bytes(
            canonical,
            headers=headers,
            timeout=timeout,
            browser_get=browser_get,
        )
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(payload, dict):
            raise ValueError("CSRC JSON endpoint must return an object")
    return payload


def _channel_metadata_url(channel_code: str) -> str:
    query = urlencode({"channelCode": str(channel_code)})
    return f"{CSRC_OFFICIAL_ORIGIN}/getLocalList?{query}"


def _search_list_url(channel_id: str, *, page: int, page_size: int) -> str:
    if page < 1 or page_size < 1:
        raise ValueError("CSRC searchList page and page_size must be positive")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", str(channel_id)):
        raise ValueError("CSRC channelId must be a 32-character hex identity")
    query = urlencode(
        {
            "_isAgg": "true",
            "_isJson": "true",
            "_pageSize": int(page_size),
            "page": int(page),
        }
    )
    return f"{CSRC_OFFICIAL_ORIGIN}/searchList/{channel_id}?{query}"


def resolve_csrc_channel(
    *,
    segment: str,
    channel_code: str,
    default_evidence_type: str,
    payload: Mapping[str, object],
) -> PolicyChannel:
    if int(payload.get("code") or 0) != 200:
        raise ValueError(f"CSRC channel metadata failed for {channel_code}")
    results = payload.get("results")
    if not isinstance(results, Mapping):
        raise ValueError("CSRC channel metadata lacks results")
    levels = results.get("channelLevel")
    if not isinstance(levels, list):
        raise ValueError("CSRC channel metadata lacks channelLevel")
    matches = [
        item
        for item in levels
        if isinstance(item, Mapping)
        and str(item.get("channelCode") or "").strip() == channel_code
    ]
    if len(matches) != 1:
        raise ValueError(
            f"CSRC channel metadata requires exactly one {channel_code} match"
        )
    item = matches[0]
    channel_id = str(item.get("channelId") or "").strip()
    channel_name = str(item.get("channelName") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", channel_id):
        raise ValueError(f"CSRC channel {channel_code} has invalid channelId")
    if not channel_name:
        raise ValueError(f"CSRC channel {channel_code} has no channelName")
    return PolicyChannel(
        segment=segment,
        channel_code=channel_code,
        channel_id=channel_id,
        channel_name=channel_name,
        default_evidence_type=default_evidence_type,
    )


def parse_csrc_search_page(
    payload: Mapping[str, object],
    *,
    channel: PolicyChannel,
    requested_page: int,
    requested_page_size: int,
) -> tuple[list[PolicyListEntry], dict[str, int]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("CSRC searchList response lacks data")
    try:
        page = int(data.get("page"))
        rows = int(data.get("rows"))
        total = int(data.get("total"))
    except (TypeError, ValueError) as exc:
        raise ValueError("CSRC searchList pagination metadata is invalid") from exc
    if requested_page_size < 1:
        raise ValueError("CSRC requested_page_size must be positive")
    if page != requested_page:
        raise ValueError(
            f"CSRC searchList page mismatch: requested={requested_page} returned={page}"
        )
    if rows < 0 or total < 0:
        raise ValueError("CSRC searchList rows/total cannot be negative")
    if total > 0 and rows <= 0:
        raise ValueError("CSRC searchList effective page capacity must be positive")
    if rows > requested_page_size:
        raise ValueError(
            "CSRC searchList effective page capacity exceeds requested page size: "
            f"requested={requested_page_size} returned={rows}"
        )
    returned_channel_id = str(data.get("channelId") or "").strip()
    if returned_channel_id and returned_channel_id != channel.channel_id:
        raise ValueError("CSRC searchList channelId drifted")

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("CSRC searchList results must be a list")
    returned = len(raw_results)
    if returned > rows:
        raise ValueError(
            "CSRC searchList returned rows exceed effective page capacity: "
            f"capacity={rows} actual={returned}"
        )
    if returned > total:
        raise ValueError("CSRC searchList returned rows exceed advertised total")

    entries: list[PolicyListEntry] = []
    seen_manuscripts: set[str] = set()
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, Mapping):
            raise ValueError("CSRC searchList item must be an object")
        code = str(item.get("channelCodeName") or "").strip()
        item_channel_id = str(item.get("channelId") or "").strip()
        if code != channel.channel_code or item_channel_id != channel.channel_id:
            raise ValueError("CSRC searchList item channel identity drifted")
        manuscript_id = str(item.get("manuscriptId") or "").strip()
        title = " ".join(str(item.get("title") or "").split())
        timestamp_text = str(item.get("publishedTimeStr") or "").strip()
        raw_url = str(item.get("url") or "").strip()
        if not manuscript_id or not title or not timestamp_text or not raw_url:
            raise ValueError("CSRC searchList item lacks canonical identity fields")
        if manuscript_id in seen_manuscripts:
            raise ValueError("CSRC searchList page contains duplicate manuscriptId")
        seen_manuscripts.add(manuscript_id)
        canonical_url = _validate_csrc_https_url(raw_url)
        parsed_url = urlparse(canonical_url)
        if not parsed_url.path.endswith("/content.shtml"):
            raise ValueError("CSRC canonical document URL must end in content.shtml")
        if canonical_url in seen_urls:
            raise ValueError("CSRC searchList page contains duplicate document URL")
        seen_urls.add(canonical_url)
        try:
            published = pd.Timestamp(timestamp_text)
        except Exception as exc:
            raise ValueError("CSRC publication timestamp is invalid") from exc
        if pd.isna(published):
            raise ValueError("CSRC publication timestamp is invalid")
        entries.append(
            PolicyListEntry(
                segment=channel.segment,
                title=title,
                publication_date=published.normalize(),
                url=canonical_url,
                default_evidence_type=channel.default_evidence_type,
                manuscript_id=manuscript_id,
                publication_timestamp=timestamp_text,
                channel_code=channel.channel_code,
                channel_id=channel.channel_id,
            )
        )

    return entries, {
        "page": page,
        "rows": rows,
        "returned": returned,
        "total": total,
    }


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
    """Legacy HTML parser retained for diagnostics only.

    Formal V4-A policy materialization uses the official JSON channel API above.
    """

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
        try:
            url = _validate_csrc_https_url(
                urljoin(page_url, str(anchor.get("href") or "").strip())
            )
        except ValueError:
            continue
        parsed = urlparse(url)
        if not parsed.path.endswith("content.shtml"):
            continue
        if url in seen:
            continue
        seen.add(url)
        context_parts = [anchor.get_text(" ", strip=True)]
        parent = anchor.parent
        if parent is not None:
            context_parts.append(parent.get_text(" ", strip=True))
        nxt = anchor.find_next(
            string=re.compile(r"20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}")
        )
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


def _document_id(entry: PolicyListEntry | str) -> str:
    if isinstance(entry, PolicyListEntry) and entry.manuscript_id:
        return f"MANUSCRIPT:{entry.manuscript_id}"
    url = entry.url if isinstance(entry, PolicyListEntry) else str(entry)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    leaf = parts[-2] if len(parts) >= 2 and parts[-1] == "content.shtml" else "document"
    return f"{leaf}:{sha256(url.encode('utf-8')).hexdigest()[:16]}"


def _evidence_type(entry: PolicyListEntry) -> str:
    if any(token in entry.title for token in _NEGATIVE_TOKENS):
        return "MAJOR_NEGATIVE_EVENT"
    return entry.default_evidence_type


def _enumerate_channel(
    channel: PolicyChannel,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    json_fetcher: Callable[[str], Mapping[str, object]],
    max_pages: int,
    page_size: int,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> tuple[list[PolicyListEntry], dict[str, object]]:
    """Enumerate the full advertised CSRC channel before date filtering.

    The official searchList endpoint does not guarantee strict publication-time
    ordering for every channel. Coverage therefore cannot rely on newest-first
    early stopping. We instead prove completeness against the endpoint's stable
    advertised total, while retaining strict identity and duplicate checks.
    """

    entries: list[PolicyListEntry] = []
    seen_manuscripts: set[str] = set()
    seen_urls: set[str] = set()
    expected_total: int | None = None
    pages_read = 0

    for page in range(1, max_pages + 1):
        url = _search_list_url(channel.channel_id, page=page, page_size=page_size)
        payload = call_with_bounded_network_retry(
            lambda: json_fetcher(url),
            attempts=retry_attempts,
            backoff_seconds=retry_backoff_seconds,
        )
        page_entries, meta = parse_csrc_search_page(
            payload,
            channel=channel,
            requested_page=page,
            requested_page_size=page_size,
        )
        pages_read += 1
        total = int(meta["total"])
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(
                f"CSRC total drift for {channel.channel_code}: "
                f"{expected_total} -> {total}"
            )

        for entry in page_entries:
            if entry.manuscript_id in seen_manuscripts:
                raise ValueError(
                    f"CSRC duplicate manuscript across pages: {entry.manuscript_id}"
                )
            if entry.url in seen_urls:
                raise ValueError(f"CSRC duplicate URL across pages: {entry.url}")
            seen_manuscripts.add(entry.manuscript_id)
            seen_urls.add(entry.url)
            if start <= entry.publication_date <= end:
                entries.append(entry)

        if expected_total == 0:
            break
        if len(seen_manuscripts) > expected_total:
            raise ValueError(
                f"CSRC fetched records exceed advertised total for {channel.channel_code}"
            )
        if len(seen_manuscripts) == expected_total:
            break
        if not page_entries:
            raise ValueError(
                f"CSRC searchList ended before advertised total for {channel.channel_code}"
            )

    if expected_total is None:
        raise ValueError(f"CSRC searchList returned no pagination metadata for {channel.channel_code}")
    if len(seen_manuscripts) != expected_total:
        raise ValueError(
            f"CSRC pagination limit reached before advertised total for {channel.channel_code}: "
            f"fetched={len(seen_manuscripts)} total={expected_total}"
        )

    return entries, {
        "pages_read": pages_read,
        "channel_code": channel.channel_code,
        "channel_id": channel.channel_id,
        "channel_name": channel.channel_name,
        "advertised_total": int(expected_total),
        "enumeration_mode": "FULL_ADVERTISED_TOTAL",
    }


def materialize_csrc_policy_archive(
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    json_fetcher: Callable[[str], Mapping[str, object]] = _fetch_json,
    article_fetcher: Callable[[str], str] = _fetch_text,
    max_pages_per_segment: int = 500,
    page_size: int = 50,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> PitMaterializationResult:
    """Materialize fixed CSRC channels through the official JSON archive API."""

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if max_pages_per_segment <= 0 or page_size <= 0:
        raise ValueError("max_pages_per_segment and page_size must be positive")
    calendar = _real_trading_calendar(trading_dates)
    captured = datetime.now(timezone.utc).isoformat()

    all_entries: dict[str, PolicyListEntry] = {}
    segment_meta: dict[str, dict[str, object]] = {}
    segment_failed: dict[str, bool] = {segment: False for segment in CSRC_CHANNELS}
    errors: list[dict[str, str]] = []

    for segment, (channel_code, default_type) in CSRC_CHANNELS.items():
        try:
            metadata_url = _channel_metadata_url(channel_code)
            metadata_payload = call_with_bounded_network_retry(
                lambda: json_fetcher(metadata_url),
                attempts=retry_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            channel = resolve_csrc_channel(
                segment=segment,
                channel_code=channel_code,
                default_evidence_type=default_type,
                payload=metadata_payload,
            )
            entries, meta = _enumerate_channel(
                channel,
                start=start,
                end=end,
                json_fetcher=json_fetcher,
                max_pages=max_pages_per_segment,
                page_size=page_size,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            segment_meta[segment] = meta
            for entry in entries:
                prior = all_entries.get(entry.url)
                if prior is not None and prior.manuscript_id != entry.manuscript_id:
                    raise ValueError("CSRC canonical URL maps to conflicting manuscript identities")
                all_entries[entry.url] = entry
        except Exception as exc:
            segment_failed[segment] = True
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{segment}:{type(exc).__name__}: {exc}",
                }
            )

    evidence_rows: list[dict[str, object]] = []
    for entry in sorted(
        all_entries.values(),
        key=lambda item: (item.publication_date, item.publication_timestamp, item.url),
    ):
        try:
            article = call_with_bounded_network_retry(
                lambda: article_fetcher(entry.url),
                attempts=retry_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            if not str(article).strip():
                raise ValueError("CSRC canonical article is empty")
            article_sha = sha256(str(article).encode("utf-8")).hexdigest()
            available_date, availability_rule = _market_available_date(
                str(entry.publication_date.date()), trading_dates=calendar
            )
        except Exception as exc:
            segment_failed[entry.segment] = True
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{entry.segment}:{entry.url}:{type(exc).__name__}: {exc}",
                }
            )
            continue
        doc_id = _document_id(entry)
        evidence_type = _evidence_type(entry)
        provenance = {
            "source_identity": POLICY_SOURCE_ID,
            "provider": POLICY_PROVIDER,
            "coverage_segment": entry.segment,
            "channel_code": entry.channel_code,
            "channel_id": entry.channel_id,
            "manuscript_id": entry.manuscript_id,
            "document_url": entry.url,
            "document_sha256": article_sha,
            "publication_timestamp": entry.publication_timestamp,
            "publication_date_source": "OFFICIAL_CSRC_JSON_CHANNEL_API",
            "evidence_available_date_semantics": "NEXT_REAL_TRADING_DATE_FOR_CONSERVATIVE_DATE_ONLY_POLICY_AVAILABILITY",
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

    coverage_rows: list[dict[str, object]] = []
    for segment, (channel_code, _) in CSRC_CHANNELS.items():
        meta = segment_meta.get(segment, {})
        coverage_rows.append(
            {
                "source_identity": POLICY_SOURCE_ID,
                "entity_id": POLICY_ENTITY_ID,
                "coverage_segment": segment,
                "coverage_start": start,
                "coverage_end": end,
                "query_status": "FAILED" if segment_failed[segment] else "COMPLETE_WINDOW",
                "channel_code": channel_code,
                "channel_id": str(meta.get("channel_id") or ""),
                "pages_read": int(meta.get("pages_read") or 0),
                "advertised_total": int(meta.get("advertised_total") or 0),
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
        "archive_protocol": "OFFICIAL_CSRC_GETLOCALLIST_SEARCHLIST_JSON_V4_SERVER_PAGE_CAPACITY",
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "coverage_segments": sorted(CSRC_CHANNELS),
        "source_coverage_complete": complete,
        "materialized_records": int(len(records)),
        "error_rows": int(len(errors)),
        "readiness_state": (
            "QUALIFIED_INPUT"
            if complete and len(records)
            else "PARTIAL_COVERAGE" if len(records) else "DATA_INSUFFICIENT"
        ),
        "captured_at_utc": captured,
        "future_prices_or_returns_used": False,
        "search_results_are_not_canonical_evidence": True,
        "document_failure_invalidates_segment_coverage": True,
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
    "CSRC_CHANNELS",
    "CSRC_LISTS",
    "PolicyChannel",
    "PolicyListEntry",
    "resolve_csrc_channel",
    "parse_csrc_search_page",
    "parse_csrc_list_page",
    "materialize_csrc_policy_archive",
]
