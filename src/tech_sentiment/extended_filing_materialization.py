from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Iterable

import pandas as pd

from .cninfo_direct import fetch_cninfo_announcements_direct
from .filing_materialization import (
    _entity_id,
    _select_primary_numeric_filing_candidates,
    _symbol_query_identity,
)
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .official_filing_extended_pit import (
    EXTENDED_FILING_PARSER_VERSION,
    build_extended_filing_fact_rows,
)
from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    download_official_document,
    extract_pdf_text,
)
from .pit_public_materialization import (
    CNINFO_SOURCE_ID,
    _market_available_date,
    _parse_document_identity,
    _real_trading_calendar,
)


EXTENDED_PIT_MATERIALIZER_VERSION = "official-filing-extended-pit-materializer-v1"


@dataclass(frozen=True)
class ExtendedFilingMaterializationResult:
    facts: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _extended_document_identity(
    *,
    source_commit: str,
    symbol: str,
    document_id: str,
    attachment_url: str,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="official-filing-extended-pit",
        producer_version=EXTENDED_FILING_PARSER_VERSION,
        source_commit=source_commit,
        source_identities=(CNINFO_SOURCE_ID,),
        query_identity={
            "symbol": symbol,
            "document_id": document_id,
            "attachment_url": attachment_url,
        },
        scope={"document_id": document_id},
    )


_SOFT_DATA_INSUFFICIENCY_MARKERS = (
    "extended filing text does not contain an explicit table unit declaration",
    "official filing has no extended PIT primitives with locally proven CNY units",
)


def _severity(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, ValueError) and any(
        marker in text for marker in _SOFT_DATA_INSUFFICIENCY_MARKERS
    ):
        return "SOFT_DATA_INSUFFICIENCY"
    return "HARD_FAILURE"


def _hard_failure_signature(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    for marker in (
        "FINANCIAL_FILING_MISSING_IMMUTABLE_ATTACHMENT_URL",
        "CNINFO request failed",
        "checkpoint receipt hash mismatch",
        "checkpoint file hash mismatch",
        "checkpoint fingerprint mismatch",
        "non-CNINFO endpoint refused",
    ):
        if marker in text:
            return f"{type(exc).__name__}:{marker}"
    return f"{type(exc).__name__}:{text[:160]}"


def _emit_progress(
    *,
    completed_symbols: int,
    total_symbols: int,
    entity_id: str,
    resumed_symbol_queries: int,
    executed_symbol_queries: int,
    resumed_documents: int,
    executed_documents: int,
    hard_failures: int,
    soft_failures: int,
    started_at: float,
    circuit_breaker_signature: str | None,
) -> None:
    elapsed = max(time.monotonic() - started_at, 0.001)
    rate = completed_symbols / elapsed
    remaining = max(total_symbols - completed_symbols, 0)
    eta_seconds = (remaining / rate) if rate > 0 else None
    print(
        json.dumps(
            {
                "event": "EXTENDED_PIT_PROGRESS",
                "completed_symbols": completed_symbols,
                "total_symbols": total_symbols,
                "entity_id": entity_id,
                "resumed_symbol_queries": resumed_symbol_queries,
                "executed_symbol_queries": executed_symbol_queries,
                "resumed_documents": resumed_documents,
                "executed_documents": executed_documents,
                "hard_failures": hard_failures,
                "soft_failures": soft_failures,
                "elapsed_seconds": round(elapsed, 1),
                "eta_seconds": None if eta_seconds is None else round(eta_seconds, 1),
                "circuit_breaker_signature": circuit_breaker_signature,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def materialize_extended_filing_facts(
    symbols: Iterable[str],
    *,
    target_start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
    warmup_years: int = 2,
    checkpoint_source_commit: str | None = None,
    legacy_checkpoint_dir: str | Path | None = None,
    legacy_query_checkpoint_source_commits: Iterable[str] | None = None,
    progress_checkpoint_source_commit: str | None = None,
    hard_failure_circuit_breaker_threshold: int = 3,
    max_financial_documents_per_symbol: int | None = None,
) -> ExtendedFilingMaterializationResult:
    """Materialize direct official-filing raw PIT primitives only.

    This function is deliberately outcome-blind and model-neutral. It preserves
    direct statement line items with document-level provenance and never creates
    model-facing CASH/DEBT aggregates or any evidence qualification.
    """

    if warmup_years < 1:
        raise ValueError("warmup_years must be >= 1")
    if hard_failure_circuit_breaker_threshold < 1:
        raise ValueError("hard_failure_circuit_breaker_threshold must be >= 1")
    if (
        max_financial_documents_per_symbol is not None
        and max_financial_documents_per_symbol < 1
    ):
        raise ValueError("max_financial_documents_per_symbol must be >= 1")

    target_start = pd.Timestamp(target_start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < target_start:
        raise ValueError("end_date must not precede target_start_date")

    query_start = target_start - pd.DateOffset(years=warmup_years)
    calendar = _real_trading_calendar(trading_dates)
    store = ImmutableCheckpointStore(checkpoint_dir)
    legacy_store = (
        ImmutableCheckpointStore(legacy_checkpoint_dir)
        if legacy_checkpoint_dir is not None
        else store
    )

    query_checkpoint_commit = str(checkpoint_source_commit or source_commit)
    legacy_query_commits: list[str] = []
    for value in (
        [query_checkpoint_commit]
        + [str(item) for item in (legacy_query_checkpoint_source_commits or [])]
    ):
        if value and value not in legacy_query_commits:
            legacy_query_commits.append(value)
    progress_commit = str(progress_checkpoint_source_commit or source_commit)
    unique_symbols = sorted({str(value).zfill(6) for value in symbols})
    if not unique_symbols:
        raise ValueError("at least one symbol is required")

    fact_parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    resumed_symbol_queries = 0
    reused_legacy_symbol_queries = 0
    legacy_query_hits_by_source_commit: Counter[str] = Counter()
    executed_symbol_queries = 0
    resumed_documents = 0
    executed_documents = 0
    hard_failure_signatures: Counter[str] = Counter()
    circuit_breaker_signature: str | None = None
    started_at = time.monotonic()
    completed_symbols = 0

    for symbol in unique_symbols:
        entity = _entity_id(symbol)

        progress_query_identity = _symbol_query_identity(
            source_commit=progress_commit,
            symbol=symbol,
            query_start=str(query_start.date()),
            query_end=str(end.date()),
        )
        loaded_query = store.load(progress_query_identity)
        if loaded_query is not None:
            raw = loaded_query.frames["announcements"]
            resumed_symbol_queries += 1
        else:
            loaded_legacy_query = None
            loaded_legacy_query_commit: str | None = None
            for legacy_commit in legacy_query_commits:
                legacy_query_identity = _symbol_query_identity(
                    source_commit=legacy_commit,
                    symbol=symbol,
                    query_start=str(query_start.date()),
                    query_end=str(end.date()),
                )
                candidate = legacy_store.load(legacy_query_identity)
                if candidate is not None:
                    loaded_legacy_query = candidate
                    loaded_legacy_query_commit = legacy_commit
                    break
            if loaded_legacy_query is not None:
                raw = loaded_legacy_query.frames["announcements"]
                resumed_symbol_queries += 1
                reused_legacy_symbol_queries += 1
                if loaded_legacy_query_commit is not None:
                    legacy_query_hits_by_source_commit[loaded_legacy_query_commit] += 1
            else:
                try:
                    raw = fetch_cninfo_announcements_direct(
                        symbol=symbol,
                        start_date=query_start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                    )
                    store.save(
                        progress_query_identity,
                        frames={"announcements": raw},
                        metadata={
                            "entity_id": entity,
                            "progress_checkpoint_source_commit": progress_commit,
                        },
                    )
                    executed_symbol_queries += 1
                except Exception as exc:
                    errors.append(
                        {
                            "entity_id": entity,
                            "document_id": "QUERY",
                            "error": f"{type(exc).__name__}: {exc}",
                            "severity": "HARD_FAILURE",
                        }
                    )
                    signature = _hard_failure_signature(exc)
                    hard_failure_signatures[signature] += 1
                    if (
                        hard_failure_signatures[signature]
                        >= hard_failure_circuit_breaker_threshold
                    ):
                        circuit_breaker_signature = signature
                    coverage_rows.append(
                        {
                            "source_identity": CNINFO_SOURCE_ID,
                            "entity_id": entity,
                            "coverage_start": target_start,
                            "coverage_end": end,
                            "query_status": "FAILED",
                            "financial_documents": 0,
                            "parsed_documents": 0,
                            "soft_data_insufficient_documents": 0,
                        }
                    )
                    completed_symbols += 1
                    _emit_progress(
                        completed_symbols=completed_symbols,
                        total_symbols=len(unique_symbols),
                        entity_id=entity,
                        resumed_symbol_queries=resumed_symbol_queries,
                        executed_symbol_queries=executed_symbol_queries,
                        resumed_documents=resumed_documents,
                        executed_documents=executed_documents,
                        hard_failures=sum(hard_failure_signatures.values()),
                        soft_failures=sum(
                            1
                            for item in errors
                            if item["severity"] == "SOFT_DATA_INSUFFICIENCY"
                        ),
                        started_at=started_at,
                        circuit_breaker_signature=circuit_breaker_signature,
                    )
                    if circuit_breaker_signature is not None:
                        break
                    continue

        candidates = _select_primary_numeric_filing_candidates(raw)
        financial_documents = 0
        parsed_documents = 0
        soft_data_insufficient_documents = 0
        symbol_failed = False

        eligible_documents_seen = 0
        for _, announcement in candidates.iterrows():
            publication = announcement["公告时间"]
            try:
                available_date, _ = _market_available_date(
                    publication,
                    trading_dates=calendar,
                )
            except ValueError:
                continue
            if available_date > end:
                continue
            if (
                max_financial_documents_per_symbol is not None
                and eligible_documents_seen >= max_financial_documents_per_symbol
            ):
                break

            eligible_documents_seen += 1
            financial_documents += 1
            document_id = "UNKNOWN"
            try:
                attachment_value = announcement.get("公告附件链接")
                attachment = (
                    ""
                    if pd.isna(attachment_value)
                    else str(attachment_value).strip()
                )
                if not attachment:
                    raise ValueError(
                        "FINANCIAL_FILING_MISSING_IMMUTABLE_ATTACHMENT_URL"
                    )

                document_id, _ = _parse_document_identity(announcement["公告链接"])
                document_identity = _extended_document_identity(
                    source_commit=progress_commit,
                    symbol=symbol,
                    document_id=document_id,
                    attachment_url=attachment,
                )
                loaded = store.load(document_identity)
                if loaded is not None:
                    facts = loaded.frames["facts"]
                    resumed_documents += 1
                else:
                    downloaded = download_official_document(attachment)
                    text = extract_pdf_text(downloaded.content)
                    facts = build_extended_filing_fact_rows(
                        entity_id=entity,
                        title=str(announcement["公告标题"]),
                        evidence_available_date=available_date,
                        publication_timestamp=publication,
                        source_identity=CNINFO_SOURCE_ID,
                        provider="CNINFO",
                        document_id=document_id,
                        revision_id=(
                            f"DOCUMENT:{document_id}:SHA256:{downloaded.sha256}"
                        ),
                        document_url=downloaded.url,
                        document_sha256=downloaded.sha256,
                        text=text,
                    )
                    if facts.empty:
                        raise ValueError(
                            "official filing has no extended PIT primitives "
                            "with locally proven CNY units"
                        )
                    store.save(
                        document_identity,
                        frames={"facts": facts},
                        metadata={
                            "entity_id": entity,
                            "document_id": document_id,
                            "publication_timestamp": str(publication),
                            "document_url": downloaded.url,
                            "document_retrieval_url": downloaded.retrieval_url,
                            "document_sha256": downloaded.sha256,
                            "progress_checkpoint_source_commit": progress_commit,
                            "evidence_qualification_changed": False,
                            "outcome_read": False,
                        },
                    )
                    executed_documents += 1

                if len(facts):
                    facts = facts.copy()
                    facts["filing_title"] = str(announcement["公告标题"])
                    fact_parts.append(facts)
                    parsed_documents += 1
            except Exception as exc:
                severity = _severity(exc)
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": document_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "severity": severity,
                    }
                )
                if severity == "SOFT_DATA_INSUFFICIENCY":
                    soft_data_insufficient_documents += 1
                else:
                    symbol_failed = True
                    signature = _hard_failure_signature(exc)
                    hard_failure_signatures[signature] += 1
                    if (
                        hard_failure_signatures[signature]
                        >= hard_failure_circuit_breaker_threshold
                    ):
                        circuit_breaker_signature = signature
                        break

        coverage_rows.append(
            {
                "source_identity": CNINFO_SOURCE_ID,
                "entity_id": entity,
                "coverage_start": target_start,
                "coverage_end": end,
                "query_status": (
                    "FAILED"
                    if symbol_failed
                    else "COMPLETE_WINDOW"
                    if parsed_documents == financial_documents
                    and financial_documents > 0
                    else "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
                ),
                "financial_documents": int(financial_documents),
                "parsed_documents": int(parsed_documents),
                "soft_data_insufficient_documents": int(
                    soft_data_insufficient_documents
                    + (1 if financial_documents == 0 else 0)
                ),
            }
        )
        completed_symbols += 1
        _emit_progress(
            completed_symbols=completed_symbols,
            total_symbols=len(unique_symbols),
            entity_id=entity,
            resumed_symbol_queries=resumed_symbol_queries,
            executed_symbol_queries=executed_symbol_queries,
            resumed_documents=resumed_documents,
            executed_documents=executed_documents,
            hard_failures=sum(hard_failure_signatures.values()),
            soft_failures=sum(
                1 for item in errors if item["severity"] == "SOFT_DATA_INSUFFICIENCY"
            ),
            started_at=started_at,
            circuit_breaker_signature=circuit_breaker_signature,
        )
        if circuit_breaker_signature is not None:
            break

    facts = (
        pd.concat(fact_parts, ignore_index=True, sort=False)
        if fact_parts
        else pd.DataFrame(columns=list(FILING_FACT_COLUMNS) + ["filing_title"])
    )
    if len(facts):
        facts["period_end"] = pd.to_datetime(
            facts["period_end"], errors="raise"
        ).dt.normalize()
        facts["evidence_available_date"] = pd.to_datetime(
            facts["evidence_available_date"], errors="raise"
        ).dt.normalize()
        facts["publication_timestamp_order"] = pd.to_datetime(
            facts["publication_timestamp"], errors="raise", utc=True
        )
        facts = (
            facts.sort_values(
                [
                    "entity_id",
                    "period_end",
                    "evidence_available_date",
                    "publication_timestamp_order",
                    "document_id",
                    "fact_type",
                ]
            )
            .drop(columns=["publication_timestamp_order"])
            .drop_duplicates(
                ["entity_id", "document_id", "revision_id", "fact_type"],
                keep="last",
            )
            .reset_index(drop=True)
        )

    coverage = pd.DataFrame(coverage_rows)
    errors_frame = pd.DataFrame(
        errors,
        columns=["entity_id", "document_id", "error", "severity"],
    )
    observed_fact_types = (
        sorted(facts["fact_type"].dropna().astype(str).unique().tolist())
        if len(facts)
        else []
    )

    summary = {
        "materializer_version": EXTENDED_PIT_MATERIALIZER_VERSION,
        "parser_version": EXTENDED_FILING_PARSER_VERSION,
        "source_identity": CNINFO_SOURCE_ID,
        "target_start_date": str(target_start.date()),
        "query_warmup_start_date": str(query_start.date()),
        "end_date": str(end.date()),
        "warmup_years": int(warmup_years),
        "query_checkpoint_source_commit": query_checkpoint_commit,
        "legacy_query_checkpoint_source_commits": legacy_query_commits,
        "legacy_query_hits_by_source_commit": dict(
            sorted(legacy_query_hits_by_source_commit.items())
        ),
        "progress_checkpoint_source_commit": progress_commit,
        "max_financial_documents_per_symbol": max_financial_documents_per_symbol,
        "symbols": len(unique_symbols),
        "filing_fact_rows": int(len(facts)),
        "observed_fact_types": observed_fact_types,
        "resumed_symbol_queries": resumed_symbol_queries,
        "reused_legacy_symbol_queries": reused_legacy_symbol_queries,
        "executed_symbol_queries": executed_symbol_queries,
        "resumed_documents": resumed_documents,
        "executed_documents": executed_documents,
        "completed_symbols": completed_symbols,
        "hard_failure_rows": int(
            sum(1 for item in errors if item["severity"] == "HARD_FAILURE")
        ),
        "soft_data_insufficiency_rows": int(
            sum(1 for item in errors if item["severity"] == "SOFT_DATA_INSUFFICIENCY")
        ),
        "hard_failure_circuit_breaker_threshold": hard_failure_circuit_breaker_threshold,
        "circuit_breaker_tripped": circuit_breaker_signature is not None,
        "circuit_breaker_signature": circuit_breaker_signature,
        "historical_materialization_is_evidence_qualification": False,
        "cash_model_field_mapping_defined": False,
        "debt_model_field_aggregation_defined": False,
        "share_count_parser_ready": False,
        "source_native_revision_sequence_qualified": False,
        "outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }

    return ExtendedFilingMaterializationResult(
        facts=facts,
        coverage=coverage,
        errors=errors_frame,
        summary=summary,
    )


__all__ = [
    "EXTENDED_PIT_MATERIALIZER_VERSION",
    "ExtendedFilingMaterializationResult",
    "materialize_extended_filing_facts",
]
