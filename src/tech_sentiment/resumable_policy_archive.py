from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .official_policy_archive import (
    CSRC_LISTS,
    POLICY_ENTITY_ID,
    POLICY_PROVIDER,
    POLICY_SOURCE_ID,
    PolicyListEntry,
    _document_id,
    _evidence_type,
    _fetch_text,
    _page_url,
    parse_csrc_list_page,
)
from .pit_public_materialization import (
    PitMaterializationResult,
    _market_available_date,
    _real_trading_calendar,
    _stable_hash,
    validate_materialized_pit_records,
)


POLICY_CHECKPOINT_VERSION = "csrc-policy-page-document-v1"


def _page_identity(
    *,
    source_commit: str,
    segment: str,
    page: int,
    url: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="csrc-policy-list-page",
        producer_version=POLICY_CHECKPOINT_VERSION,
        source_commit=source_commit,
        source_identities=(POLICY_SOURCE_ID,),
        query_identity={"segment": segment, "page": page, "url": url},
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
        query_identity={"url": entry.url, "segment": entry.segment},
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
            }
            for entry in entries
        ],
        columns=["segment", "title", "publication_date", "url", "default_evidence_type"],
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
            )
        )
    return rows


def materialize_csrc_policy_archive_resumable(
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
    fetcher: Callable[[str], str] = _fetch_text,
    max_pages_per_segment: int = 500,
) -> PitMaterializationResult:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if max_pages_per_segment <= 0:
        raise ValueError("max_pages_per_segment must be positive")
    calendar = _real_trading_calendar(trading_dates)
    store = ImmutableCheckpointStore(checkpoint_dir)
    captured = datetime.now(timezone.utc).isoformat()

    all_entries: dict[str, PolicyListEntry] = {}
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    resumed_pages = 0
    executed_pages = 0
    resumed_documents = 0
    executed_documents = 0

    for segment, (base_url, default_type) in CSRC_LISTS.items():
        reached_start = False
        pages_read = 0
        segment_failed = False
        for page in range(max_pages_per_segment):
            url = _page_url(base_url, page)
            identity = _page_identity(
                source_commit=source_commit,
                segment=segment,
                page=page,
                url=url,
                start=start,
                end=end,
            )
            loaded = store.load(identity)
            if loaded is not None:
                entries = _frame_entries(loaded.frames["entries"])
                undated = int(loaded.receipt.get("metadata", {}).get("undated_links") or 0)
                resumed_pages += 1
            else:
                try:
                    html = fetcher(url)
                    entries, undated = parse_csrc_list_page(
                        html,
                        page_url=url,
                        segment=segment,
                        default_evidence_type=default_type,
                    )
                    store.save(
                        identity,
                        frames={"entries": _entries_frame(entries)},
                        metadata={"undated_links": int(undated)},
                    )
                    executed_pages += 1
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
            }
        )

    evidence_parts: list[pd.DataFrame] = []
    for entry in sorted(all_entries.values(), key=lambda item: (item.publication_date, item.url)):
        identity = _document_identity(source_commit=source_commit, entry=entry)
        loaded = store.load(identity)
        if loaded is not None:
            evidence_parts.append(loaded.frames["evidence"])
            resumed_documents += 1
            continue
        try:
            article = fetcher(entry.url)
            article_sha = sha256(article.encode("utf-8")).hexdigest()
            available_date, availability_rule = _market_available_date(
                str(entry.publication_date.date()), trading_dates=calendar
            )
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
                        "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
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
                        "captured_at_utc": captured,
                    }
                ]
            )
            evidence_frame = validate_materialized_pit_records(evidence_frame)
            store.save(
                identity,
                frames={"evidence": evidence_frame},
                metadata={"document_sha256": article_sha},
            )
            evidence_parts.append(evidence_frame)
            executed_documents += 1
        except Exception as exc:
            errors.append(
                {
                    "source_identity": POLICY_SOURCE_ID,
                    "entity_id": POLICY_ENTITY_ID,
                    "error": f"{entry.segment}:{entry.url}:{type(exc).__name__}: {exc}",
                }
            )

    records = (
        validate_materialized_pit_records(pd.concat(evidence_parts, ignore_index=True, sort=False))
        if evidence_parts
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
    }
    return PitMaterializationResult(
        records=records,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["source_identity", "entity_id", "error"]),
        summary=summary,
    )


__all__ = [
    "POLICY_CHECKPOINT_VERSION",
    "materialize_csrc_policy_archive_resumable",
]
