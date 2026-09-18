from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .cninfo_direct import fetch_cninfo_announcements_direct
from .filing_materialization import _symbol_query_identity
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .issuer_earnings_pit import (
    EARNINGS_CLASSIFIER_VERSION,
    classify_explicit_issuer_earnings_direction,
)
from .official_filing_facts import download_official_document, extract_pdf_text
from .pit_public_materialization import (
    CNINFO_PROVIDER,
    CNINFO_SOURCE_ID,
    _market_available_date,
    _parse_document_identity,
    _real_trading_calendar,
    _stable_hash,
    classify_cninfo_title,
    validate_materialized_pit_records,
)


EARNINGS_MATERIALIZER_VERSION = "cninfo-issuer-earnings-direction-v1"


@dataclass(frozen=True)
class EarningsDirectionMaterializationResult:
    directions: pd.DataFrame
    evidence: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _entity_id(symbol: str) -> str:
    code = str(symbol).zfill(6)
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def _direction_document_identity(
    *, source_commit: str, symbol: str, document_id: str, attachment_url: str
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="issuer-earnings-direction-document",
        producer_version=EARNINGS_MATERIALIZER_VERSION,
        source_commit=source_commit,
        source_identities=(CNINFO_SOURCE_ID,),
        query_identity={
            "symbol": symbol,
            "document_id": document_id,
            "attachment_url": attachment_url,
        },
        scope={"document_id": document_id},
    )


def _to_evidence(directions: pd.DataFrame) -> pd.DataFrame:
    if directions.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, item in directions.iterrows():
        event_date = pd.Timestamp(item["event_date"]).normalize()
        available = pd.Timestamp(item["evidence_available_date"]).normalize()
        payload = {
            "earnings_expectation_direction": str(item["earnings_expectation_direction"]),
            "classification_basis": "EXPLICIT_ISSUER_GUIDANCE_TOKEN_IN_EXACT_OFFICIAL_DOCUMENT",
            "classifier_version": str(item["classifier_version"]),
            "source_document_sha256": str(item["document_sha256"]),
            "price_or_return_used": False,
            "numeric_threshold_used": False,
        }
        provenance = {
            "source_identity": CNINFO_SOURCE_ID,
            "provider": CNINFO_PROVIDER,
            "document_url": str(item["document_url"]),
            "document_retrieval_url": str(
                item.get("document_retrieval_url") or item["document_url"]
            ),
            "document_id": str(item["document_id"]),
            "document_sha256": str(item["document_sha256"]),
            "classifier_version": str(item["classifier_version"]),
            "availability_rule": str(item["availability_rule"]),
            "source_identity_inherited_not_new_evidence_source": True,
            "future_prices_or_returns_used": False,
            "numeric_threshold_used": False,
        }
        identity = {
            "entity_id": str(item["entity_id"]),
            "document_id": str(item["document_id"]),
            "document_sha256": str(item["document_sha256"]),
            "direction": str(item["earnings_expectation_direction"]),
            "available": str(available.date()),
        }
        rows.append(
            {
                "evidence_id": f"issuer-earnings:{_stable_hash(identity)}",
                "entity_id": str(item["entity_id"]),
                "evidence_type": "ISSUER_EARNINGS_DIRECTION",
                "event_date": event_date,
                "evidence_available_date": available,
                "source_identity": CNINFO_SOURCE_ID,
                "provider": CNINFO_PROVIDER,
                "document_id": str(item["document_id"]),
                "revision_id": f"DOCUMENT_SHA256:{item['document_sha256']}",
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash({"identity": identity, "payload": payload}),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "source_url_identity": str(item["document_url"]),
            }
        )
    return validate_materialized_pit_records(pd.DataFrame(rows))


def materialize_cninfo_earnings_directions(
    symbols: Iterable[str],
    *,
    query_start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
) -> EarningsDirectionMaterializationResult:
    start = pd.Timestamp(query_start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    calendar = _real_trading_calendar(trading_dates)
    store = ImmutableCheckpointStore(checkpoint_dir)
    unique_symbols = sorted({str(value).zfill(6) for value in symbols})
    rows: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    resumed_docs = 0
    executed_docs = 0

    for symbol in unique_symbols:
        entity = _entity_id(symbol)
        query_identity = _symbol_query_identity(
            source_commit=source_commit,
            symbol=symbol,
            query_start=str(start.date()),
            query_end=str(end.date()),
        )
        loaded_query = store.load(query_identity)
        if loaded_query is not None:
            raw = loaded_query.frames["announcements"]
        else:
            try:
                raw = fetch_cninfo_announcements_direct(
                    symbol=symbol,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                store.save(
                    query_identity,
                    frames={"announcements": raw},
                    metadata={"entity_id": entity},
                )
            except Exception as exc:
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": "QUERY",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                coverage_rows.append(
                    {
                        "source_identity": CNINFO_SOURCE_ID,
                        "entity_id": entity,
                        "coverage_start": start,
                        "coverage_end": end,
                        "query_status": "FAILED",
                        "forecast_documents": 0,
                        "direction_documents": 0,
                    }
                )
                continue

        candidates = raw[
            raw["公告标题"].map(classify_cninfo_title).eq("ISSUER_EARNINGS_FORECAST")
        ].copy()
        forecast_documents = 0
        direction_documents = 0
        failed = False
        for _, announcement in candidates.iterrows():
            try:
                available_date, availability_rule = _market_available_date(
                    announcement["公告时间"], trading_dates=calendar
                )
            except ValueError:
                continue
            if available_date > end:
                continue
            forecast_documents += 1
            document_id = "UNKNOWN"
            try:
                document_id, _ = _parse_document_identity(announcement["公告链接"])
                attachment_value = announcement.get("公告附件链接")
                attachment = "" if pd.isna(attachment_value) else str(attachment_value).strip()
                if not attachment:
                    raise ValueError("earnings forecast lacks immutable attachment URL")
                identity = _direction_document_identity(
                    source_commit=source_commit,
                    symbol=symbol,
                    document_id=document_id,
                    attachment_url=attachment,
                )
                loaded = store.load(identity)
                if loaded is not None:
                    direction_frame = loaded.frames["direction"]
                    resumed_docs += 1
                else:
                    downloaded = download_official_document(attachment)
                    text = extract_pdf_text(downloaded.content)
                    direction = classify_explicit_issuer_earnings_direction(
                        f"{announcement['公告标题']}\n{text}"
                    )
                    if direction == "UNKNOWN":
                        raise ValueError(
                            "issuer forecast document has no recognized explicit guidance direction"
                        )
                    direction_frame = pd.DataFrame(
                        [
                            {
                                "entity_id": entity,
                                "document_id": document_id,
                                "event_date": pd.Timestamp(
                                    pd.to_datetime(announcement["公告时间"], errors="raise")
                                ).normalize(),
                                "earnings_expectation_direction": direction,
                                "evidence_available_date": available_date,
                                "document_url": downloaded.url,
                                "document_retrieval_url": downloaded.retrieval_url or downloaded.url,
                                "document_sha256": downloaded.sha256,
                                "classifier_version": EARNINGS_CLASSIFIER_VERSION,
                                "availability_rule": availability_rule,
                            }
                        ]
                    )
                    store.save(
                        identity,
                        frames={"direction": direction_frame},
                        metadata={
                            "document_url": downloaded.url,
                            "document_retrieval_url": downloaded.retrieval_url or downloaded.url,
                            "document_sha256": downloaded.sha256,
                        },
                    )
                    executed_docs += 1
                if len(direction_frame):
                    rows.extend(direction_frame.to_dict("records"))
                    direction_documents += 1
            except Exception as exc:
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": document_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                failed = True

        coverage_rows.append(
            {
                "source_identity": CNINFO_SOURCE_ID,
                "entity_id": entity,
                "coverage_start": start,
                "coverage_end": end,
                "query_status": "FAILED" if failed else "COMPLETE_WINDOW",
                "forecast_documents": int(forecast_documents),
                "direction_documents": int(direction_documents),
            }
        )

    directions = pd.DataFrame(rows)
    if len(directions):
        directions["event_date"] = pd.to_datetime(directions["event_date"], errors="raise").dt.normalize()
        directions["evidence_available_date"] = pd.to_datetime(
            directions["evidence_available_date"], errors="raise"
        ).dt.normalize()
        if directions.duplicated(["entity_id", "document_id"]).any():
            raise ValueError("earnings direction materialization has duplicate document identities")
    evidence = _to_evidence(directions)
    coverage = pd.DataFrame(coverage_rows)
    complete = bool(len(coverage)) and bool(
        coverage["query_status"].eq("COMPLETE_WINDOW").all()
    )
    summary = {
        "source_identity": CNINFO_SOURCE_ID,
        "query_start_date": str(start.date()),
        "end_date": str(end.date()),
        "symbols": len(unique_symbols),
        "forecast_documents": int(coverage["forecast_documents"].sum()) if len(coverage) else 0,
        "direction_documents": int(coverage["direction_documents"].sum()) if len(coverage) else 0,
        "canonical_evidence_records": int(len(evidence)),
        "source_window_and_direction_extraction_complete": complete,
        "readiness_state": "QUALIFIED_INPUT" if complete else (
            "PARTIAL_COVERAGE" if len(coverage) else "DATA_INSUFFICIENT"
        ),
        "resumed_documents": resumed_docs,
        "executed_documents": executed_docs,
        "unknown_is_not_not_down": True,
        "numeric_threshold_used": False,
        "price_or_return_used": False,
    }
    return EarningsDirectionMaterializationResult(
        directions=directions,
        evidence=evidence,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["entity_id", "document_id", "error"]),
        summary=summary,
    )


__all__ = [
    "EARNINGS_MATERIALIZER_VERSION",
    "EarningsDirectionMaterializationResult",
    "materialize_cninfo_earnings_directions",
]
