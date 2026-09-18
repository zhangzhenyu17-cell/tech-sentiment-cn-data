from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pandas as pd

from .bounded_retry import call_with_bounded_network_retry
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .official_policy_archive import (
    CSRC_CHANNELS,
    POLICY_ENTITY_ID,
    POLICY_PROVIDER,
    POLICY_SOURCE_ID,
    PolicyChannel,
    PolicyListEntry,
    _channel_metadata_url,
    _document_id,
    _evidence_type,
    _fetch_json,
    _fetch_text,
    _search_list_url,
    parse_csrc_search_page,
    resolve_csrc_channel,
)
from .pit_public_materialization import (
    PitMaterializationResult,
    _market_available_date,
    _real_trading_calendar,
    _stable_hash,
    validate_materialized_pit_records,
)


POLICY_CHECKPOINT_VERSION = "csrc-policy-json-page-document-v2"


def _page_identity(
    *,
    source_commit: str,
    channel: PolicyChannel,
    page: int,
    page_size: int,
    url: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="csrc-policy-json-list-page",
        producer_version=POLICY_CHECKPOINT_VERSION,
        source_commit=source_commit,
        source_identities=(POLICY_SOURCE_ID,),
        query_identity={
            "segment": channel.segment,
            "channel_code": channel.channel_code,
            "channel_id": channel.channel_id,
            "page": page,
            "page_size": page_size,
            "url": url,
        },
        scope={"start_date": str(start.date()), "end_date": str(end.date())},
    )


def _document_identity(
    *, source_commit: str, entry: PolicyListEntry
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="csrc-policy-document",
        producer_version=POLICY_CHECKPOINT_VERSION,
        source_commit=source_commit,
        source_identities=(POLICY_SOURCE_ID,),
        query_identity={
            "url": entry.url,
            "segment": entry.segment,
            "channel_code": entry.channel_code,
            "channel_id": entry.channel_id,
            "manuscript_id": entry.manuscript_id,
            "publication_timestamp": entry.publication_timestamp,
        },
        scope={"publication_date": str(entry.publication_date.date())},
    )


def _entries_frame(entries: list[PolicyListEntry]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "segment": entry.segment,
                "title": entry.title,
                "publication_date": entry.publication_date,
                "url": entry.url,
                "default_evidence_type": entry.default_evidence_type,
                "manuscript_id": entry.manuscript_id,
                "publication_timestamp": entry.publication_timestamp,
                "channel_code": entry.channel_code,
                "channel_id": entry.channel_id,
            }
            for entry in entries
        ],
        columns=[
            "segment",
            "title",
            "publication_date",
            "url",
            "default_evidence_type",
            "manuscript_id",
            "publication_timestamp",
            "channel_code",
            "channel_id",
        ],
    )


def _frame_entries(frame: pd.DataFrame) -> list[PolicyListEntry]:
    rows: list[PolicyListEntry] = []
    for _, row in frame.iterrows():
        rows.append(
            PolicyListEntry(
                segment=str(row["segment"]),
                title=str(row["title"]),
                publication_date=pd.Timestamp(row["publication_date"]).normalize(),
                url=str(row["url"]),
                default_evidence_type=str(row["default_evidence_type"]),
                manuscript_id=str(row["manuscript_id"]),
                publication_timestamp=str(row["publication_timestamp"]),
                channel_code=str(row["channel_code"]),
                channel_id=str(row["channel_id"]),
            )
        )
    return rows


def _materialize_document(
    *,
    entry: PolicyListEntry,
    calendar: pd.DatetimeIndex,
    article_fetcher: Callable[[str], str],
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> tuple[pd.DataFrame, str]:
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
    evidence_frame = pd.DataFrame(
        [
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
                "provenance": json.dumps(
                    provenance, ensure_ascii=False, sort_keys=True
                ),
                "ingestion_identity": _stable_hash(
                    {
                        "entity_id": POLICY_ENTITY_ID,
                        "document_id": doc_id,
                        "document_sha256": article_sha,
                        "event_date": str(entry.publication_date.date()),
                        "available": str(available_date.date()),
                        "evidence_type": evidence_type,
                    }
                ),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "title": entry.title,
                "source_url_identity": entry.url,
            }
        ]
    )
    return validate_materialized_pit_records(evidence_frame), article_sha


def materialize_csrc_policy_archive_resumable(
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
    json_fetcher: Callable[[str], Mapping[str, object]] = _fetch_json,
    article_fetcher: Callable[[str], str] = _fetch_text,
    max_pages_per_segment: int = 500,
    page_size: int = 50,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> PitMaterializationResult:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if max_pages_per_segment <= 0 or page_size <= 0:
        raise ValueError("max_pages_per_segment and page_size must be positive")
    calendar = _real_trading_calendar(trading_dates)
    store = ImmutableCheckpointStore(checkpoint_dir)

    all_entries: dict[str, PolicyListEntry] = {}
    segment_meta: dict[str, dict[str, object]] = {}
    segment_failed: dict[str, bool] = {segment: False for segment in CSRC_CHANNELS}
    errors: list[dict[str, str]] = []
    resumed_pages = 0
    executed_pages = 0
    resumed_documents = 0
    executed_documents = 0

    for segment, (channel_code, default_type) in CSRC_CHANNELS.items():
        try:
            metadata_payload = call_with_bounded_network_retry(
                lambda: json_fetcher(_channel_metadata_url(channel_code)),
                attempts=retry_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            channel = resolve_csrc_channel(
                segment=segment,
                channel_code=channel_code,
                default_evidence_type=default_type,
                payload=metadata_payload,
            )
            expected_total: int | None = None
            pages_read = 0
            seen_manuscripts: set[str] = set()
            seen_urls: set[str] = set()
            reached_start = False

            for page in range(1, max_pages_per_segment + 1):
                url = _search_list_url(
                    channel.channel_id, page=page, page_size=page_size
                )
                identity = _page_identity(
                    source_commit=source_commit,
                    channel=channel,
                    page=page,
                    page_size=page_size,
                    url=url,
                    start=start,
                    end=end,
                )
                loaded = store.load(identity)
                if loaded is not None:
                    entries = _frame_entries(loaded.frames["entries"])
                    metadata = loaded.receipt.get("metadata", {})
                    total = int(metadata.get("total") or 0)
                    returned_page = int(metadata.get("page") or 0)
                    rows = int(metadata.get("rows") or 0)
                    if returned_page != page or rows != len(entries):
                        raise ValueError("cached CSRC page metadata is inconsistent")
                    resumed_pages += 1
                else:
                    payload = call_with_bounded_network_retry(
                        lambda: json_fetcher(url),
                        attempts=retry_attempts,
                        backoff_seconds=retry_backoff_seconds,
                    )
                    entries, meta = parse_csrc_search_page(
                        payload, channel=channel, requested_page=page
                    )
                    total = int(meta["total"])
                    rows = int(meta["rows"])
                    store.save(
                        identity,
                        frames={"entries": _entries_frame(entries)},
                        metadata={"total": total, "page": page, "rows": rows},
                    )
                    executed_pages += 1
                pages_read += 1

                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise ValueError(
                        f"CSRC total drift for {channel.channel_code}: "
                        f"{expected_total} -> {total}"
                    )

                for entry in entries:
                    if entry.manuscript_id in seen_manuscripts:
                        raise ValueError(
                            f"CSRC duplicate manuscript across pages: {entry.manuscript_id}"
                        )
                    if entry.url in seen_urls:
                        raise ValueError(
                            f"CSRC duplicate URL across pages: {entry.url}"
                        )
                    seen_manuscripts.add(entry.manuscript_id)
                    seen_urls.add(entry.url)
                    if start <= entry.publication_date <= end:
                        prior = all_entries.get(entry.url)
                        if prior is not None and prior.manuscript_id != entry.manuscript_id:
                            raise ValueError(
                                "CSRC canonical URL maps to conflicting manuscript identities"
                            )
                        all_entries[entry.url] = entry

                if entries and min(item.publication_date for item in entries) <= start:
                    reached_start = True
                    break
                if expected_total == 0 or len(seen_manuscripts) >= expected_total:
                    reached_start = True
                    break
                if not entries:
                    raise ValueError(
                        f"CSRC searchList ended before advertised total for {channel.channel_code}"
                    )

            if not reached_start:
                raise ValueError(
                    f"CSRC pagination limit reached before start date for {channel.channel_code}"
                )
            segment_meta[segment] = {
                "pages_read": pages_read,
                "channel_code": channel.channel_code,
                "channel_id": channel.channel_id,
                "channel_name": channel.channel_name,
                "advertised_total": int(expected_total or 0),
            }
        except Exception as exc:
            segment_failed[segment] = True
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{segment}:{type(exc).__name__}: {exc}",
                }
            )

    evidence_parts: list[pd.DataFrame] = []
    for entry in sorted(
        all_entries.values(),
        key=lambda item: (item.publication_date, item.publication_timestamp, item.url),
    ):
        identity = _document_identity(source_commit=source_commit, entry=entry)
        loaded = store.load(identity)
        if loaded is not None:
            evidence_parts.append(loaded.frames["evidence"])
            resumed_documents += 1
            continue
        try:
            evidence_frame, article_sha = _materialize_document(
                entry=entry,
                calendar=calendar,
                article_fetcher=article_fetcher,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            store.save(
                identity,
                frames={"evidence": evidence_frame},
                metadata={"document_sha256": article_sha},
            )
            evidence_parts.append(evidence_frame)
            executed_documents += 1
        except Exception as exc:
            segment_failed[entry.segment] = True
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{entry.segment}:{entry.url}:{type(exc).__name__}: {exc}",
                }
            )

    records = (
        validate_materialized_pit_records(
            pd.concat(evidence_parts, ignore_index=True, sort=False)
        )
        if evidence_parts
        else pd.DataFrame()
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
                "query_status": (
                    "FAILED" if segment_failed[segment] else "COMPLETE_WINDOW"
                ),
                "channel_code": channel_code,
                "channel_id": str(meta.get("channel_id") or ""),
                "pages_read": int(meta.get("pages_read") or 0),
                "advertised_total": int(meta.get("advertised_total") or 0),
            }
        )
    coverage = pd.DataFrame(coverage_rows)
    complete = bool(len(coverage)) and bool(
        coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all()
    )
    summary = {
        "source_identity": POLICY_SOURCE_ID,
        "provider": POLICY_PROVIDER,
        "archive_protocol": "OFFICIAL_CSRC_GETLOCALLIST_SEARCHLIST_JSON_V2",
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
        "checkpoint_schema": POLICY_CHECKPOINT_VERSION,
        "resumed_pages": resumed_pages,
        "executed_pages": executed_pages,
        "resumed_documents": resumed_documents,
        "executed_documents": executed_documents,
        "future_prices_or_returns_used": False,
        "search_results_are_not_canonical_evidence": True,
        "document_failure_invalidates_segment_coverage": True,
    }
    return PitMaterializationResult(
        records=records,
        coverage=coverage,
        errors=pd.DataFrame(
            errors, columns=["source_identity", "entity_id", "error"]
        ),
        summary=summary,
    )


__all__ = [
    "POLICY_CHECKPOINT_VERSION",
    "materialize_csrc_policy_archive_resumable",
]
