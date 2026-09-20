from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any

import pandas as pd

from .issuer_earnings_pit import (
    EARNINGS_CLASSIFIER_VERSION,
    classify_explicit_issuer_earnings_direction,
)
from .official_filing_facts import download_official_document, extract_pdf_text
from .pit_public_materialization import _stable_hash, validate_materialized_pit_records


EXCHANGE_EARNINGS_SOURCES = {
    "SSE_ANNOUNCEMENT_ARCHIVE",
    "SZSE_ANNOUNCEMENT_ARCHIVE",
}

_REQUIRED_COLUMNS = {
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
    "title",
    "source_url_identity",
}


@dataclass(frozen=True)
class ExchangeEarningsDirectionResult:
    evidence: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    unclassified: pd.DataFrame
    summary: dict[str, object]


def _default_document_loader(url: str) -> dict[str, str]:
    downloaded = download_official_document(url)
    return {
        "document_url": str(downloaded.url),
        "document_retrieval_url": str(downloaded.retrieval_url or downloaded.url),
        "document_sha256": str(downloaded.sha256),
        "transport_sha256": str(downloaded.transport_sha256 or downloaded.sha256),
        "transport_encoding": str(downloaded.transport_encoding or "identity"),
        "text": extract_pdf_text(downloaded.content),
    }


def _load_document(
    loader: Callable[[str], Mapping[str, object]],
    url: str,
) -> dict[str, str]:
    payload = loader(url)
    if not isinstance(payload, Mapping):
        raise ValueError("document_loader must return a mapping")
    required = {"document_url", "document_retrieval_url", "document_sha256", "text"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"document_loader missing fields: {sorted(missing)}")
    out = {key: str(payload[key]) for key in required}
    for optional_key in ("transport_sha256", "transport_encoding"):
        value = payload.get(optional_key)
        if value is not None and str(value):
            out[optional_key] = str(value)
    if not out["document_url"] or not out["document_sha256"]:
        raise ValueError("document_loader returned empty immutable document identity")
    return out


def _availability_rule(raw_provenance: object) -> str:
    parsed = json.loads(str(raw_provenance))
    if not isinstance(parsed, dict):
        raise ValueError("issuer provenance must be a JSON object")
    rule = str(parsed.get("availability_rule") or "").strip()
    if not rule:
        raise ValueError("issuer provenance missing availability_rule")
    return rule


def materialize_registered_exchange_earnings_directions(
    issuer_records: pd.DataFrame,
    *,
    document_loader: Callable[[str], Mapping[str, object]] | None = None,
) -> ExchangeEarningsDirectionResult:
    """Extract frozen explicit earnings-direction semantics from SSE/SZSE records.

    This is an engineering bridge only. It reuses already-materialized registered
    issuer records, their event/available dates, and the frozen
    issuer-explicit-guidance-v1 token classifier. It does not query a new issuer
    universe and it does not consult prices, returns, model outputs, or outcomes.

    Formal evidence eligibility is unchanged until a separately authorized
    pipeline explicitly integrates and verifies the resulting records.
    """

    missing = _REQUIRED_COLUMNS - set(issuer_records.columns)
    if missing:
        raise ValueError(f"issuer_records missing columns: {sorted(missing)}")

    loader = document_loader or _default_document_loader
    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    unclassified: list[dict[str, object]] = []

    source = issuer_records[
        issuer_records["source_identity"].astype(str).isin(EXCHANGE_EARNINGS_SOURCES)
        & issuer_records["evidence_type"].astype(str).eq("ISSUER_EARNINGS_FORECAST")
        & issuer_records["availability_state"].astype(str).eq(
            "HISTORICAL_RECONSTRUCTABLE"
        )
    ].copy()

    if source.empty:
        empty = pd.DataFrame()
        return ExchangeEarningsDirectionResult(
            evidence=empty,
            coverage=pd.DataFrame(
                columns=[
                    "source_identity",
                    "entity_id",
                    "forecast_documents",
                    "direction_documents",
                    "unclassified_documents",
                    "error_documents",
                ]
            ),
            errors=pd.DataFrame(
                columns=["source_identity", "entity_id", "document_id", "error"]
            ),
            unclassified=pd.DataFrame(
                columns=[
                    "source_identity",
                    "entity_id",
                    "document_id",
                    "title",
                    "evidence_available_date",
                    "source_url_identity",
                    "document_sha256",
                    "classifier_version",
                    "reason",
                ]
            ),
            summary={
                "status": "NO_ELIGIBLE_EXCHANGE_FORECAST_RECORDS",
                "forecast_documents": 0,
                "direction_documents": 0,
                "unclassified_documents": 0,
                "error_documents": 0,
                "source_archive_refetch": False,
                "new_source_identity_added": False,
                "evidence_qualification_changed": False,
                "outcomes_read": False,
            },
        )

    source["event_date"] = pd.to_datetime(source["event_date"], errors="raise").dt.normalize()
    source["evidence_available_date"] = pd.to_datetime(
        source["evidence_available_date"], errors="raise"
    ).dt.normalize()
    if source.duplicated(["source_identity", "document_id"]).any():
        raise ValueError("exchange issuer forecast records contain duplicate source/document identity")

    for _, record in source.iterrows():
        source_identity = str(record["source_identity"])
        entity_id = str(record["entity_id"])
        document_id = str(record["document_id"])
        url = str(record["source_url_identity"] or "").strip()
        if not url:
            errors.append(
                {
                    "source_identity": source_identity,
                    "entity_id": entity_id,
                    "document_id": document_id,
                    "error": "ValueError: source_url_identity is empty",
                }
            )
            continue

        try:
            document = _load_document(loader, url)
            direction = classify_explicit_issuer_earnings_direction(
                f"{record['title']}\n{document['text']}"
            )
            if direction == "UNKNOWN":
                unclassified.append(
                    {
                        "source_identity": source_identity,
                        "entity_id": entity_id,
                        "document_id": document_id,
                        "title": str(record["title"]),
                        "evidence_available_date": record["evidence_available_date"],
                        "source_url_identity": url,
                        "document_sha256": document["document_sha256"],
                        "classifier_version": EARNINGS_CLASSIFIER_VERSION,
                        "reason": "UNCLASSIFIED_NO_EXPLICIT_UNAMBIGUOUS_DIRECTION",
                    }
                )
                continue

            availability_rule = _availability_rule(record["provenance"])
            payload = {
                "earnings_expectation_direction": direction,
                "classification_basis": "EXPLICIT_ISSUER_GUIDANCE_TOKEN_IN_EXACT_OFFICIAL_DOCUMENT",
                "classifier_version": EARNINGS_CLASSIFIER_VERSION,
                "source_document_sha256": document["document_sha256"],
                "price_or_return_used": False,
                "numeric_threshold_used": False,
            }
            provenance = {
                "source_identity": source_identity,
                "provider": str(record["provider"]),
                "source_record_evidence_id": str(record["evidence_id"]),
                "source_revision_id": str(record["revision_id"]),
                "document_url": document["document_url"],
                "document_retrieval_url": document["document_retrieval_url"],
                "document_id": document_id,
                "document_sha256": document["document_sha256"],
                "transport_sha256": document.get(
                    "transport_sha256", document["document_sha256"]
                ),
                "transport_encoding": document.get("transport_encoding", "identity"),
                "classifier_version": EARNINGS_CLASSIFIER_VERSION,
                "availability_rule": availability_rule,
                "source_identity_inherited_not_new_evidence_source": True,
                "source_archive_refetch": False,
                "future_prices_or_returns_used": False,
                "numeric_threshold_used": False,
            }
            identity = {
                "source_identity": source_identity,
                "entity_id": entity_id,
                "document_id": document_id,
                "document_sha256": document["document_sha256"],
                "direction": direction,
                "available": str(record["evidence_available_date"].date()),
            }
            rows.append(
                {
                    "evidence_id": f"issuer-earnings-exchange:{_stable_hash(identity)}",
                    "entity_id": entity_id,
                    "evidence_type": "ISSUER_EARNINGS_DIRECTION",
                    "event_date": record["event_date"],
                    "evidence_available_date": record["evidence_available_date"],
                    "source_identity": source_identity,
                    "provider": str(record["provider"]),
                    "document_id": document_id,
                    "revision_id": f"DOCUMENT_SHA256:{document['document_sha256']}",
                    "provenance": json.dumps(
                        provenance, ensure_ascii=False, sort_keys=True
                    ),
                    "ingestion_identity": _stable_hash(
                        {"identity": identity, "payload": payload}
                    ),
                    "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                    "evidence_payload": json.dumps(
                        payload, ensure_ascii=False, sort_keys=True
                    ),
                    "source_url_identity": document["document_url"],
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "source_identity": source_identity,
                    "entity_id": entity_id,
                    "document_id": document_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    evidence = pd.DataFrame(rows)
    if len(evidence):
        evidence = validate_materialized_pit_records(evidence)

    error_frame = pd.DataFrame(
        errors,
        columns=["source_identity", "entity_id", "document_id", "error"],
    )
    unclassified_frame = pd.DataFrame(
        unclassified,
        columns=[
            "source_identity",
            "entity_id",
            "document_id",
            "title",
            "evidence_available_date",
            "source_url_identity",
            "document_sha256",
            "classifier_version",
            "reason",
        ],
    )

    coverage_rows: list[dict[str, object]] = []
    for (source_identity, entity_id), part in source.groupby(
        ["source_identity", "entity_id"], sort=True
    ):
        doc_ids = set(part["document_id"].astype(str))
        direction_ids = (
            set(
                evidence.loc[
                    evidence["source_identity"].astype(str).eq(str(source_identity))
                    & evidence["entity_id"].astype(str).eq(str(entity_id)),
                    "document_id",
                ].astype(str)
            )
            if len(evidence)
            else set()
        )
        unclassified_ids = set(
            unclassified_frame.loc[
                unclassified_frame["source_identity"].astype(str).eq(str(source_identity))
                & unclassified_frame["entity_id"].astype(str).eq(str(entity_id)),
                "document_id",
            ].astype(str)
        )
        error_ids = set(
            error_frame.loc[
                error_frame["source_identity"].astype(str).eq(str(source_identity))
                & error_frame["entity_id"].astype(str).eq(str(entity_id)),
                "document_id",
            ].astype(str)
        )
        coverage_rows.append(
            {
                "source_identity": str(source_identity),
                "entity_id": str(entity_id),
                "forecast_documents": len(doc_ids),
                "direction_documents": len(direction_ids),
                "unclassified_documents": len(unclassified_ids),
                "error_documents": len(error_ids),
            }
        )
    coverage = pd.DataFrame(coverage_rows)

    return ExchangeEarningsDirectionResult(
        evidence=evidence,
        coverage=coverage,
        errors=error_frame,
        unclassified=unclassified_frame,
        summary={
            "status": "ENGINEERING_MATERIALIZATION_COMPLETE_NOT_FORMAL_EVIDENCE",
            "sources": sorted(EXCHANGE_EARNINGS_SOURCES),
            "classifier_version": EARNINGS_CLASSIFIER_VERSION,
            "forecast_documents": int(len(source)),
            "direction_documents": int(len(evidence)),
            "unclassified_documents": int(len(unclassified_frame)),
            "error_documents": int(len(error_frame)),
            "source_archive_refetch": False,
            "new_source_identity_added": False,
            "event_or_available_date_changed": False,
            "numeric_threshold_used": False,
            "price_or_return_used": False,
            "outcomes_read": False,
            "formal_bundle_integration_performed": False,
            "evidence_qualification_changed": False,
        },
    )


__all__ = [
    "EXCHANGE_EARNINGS_SOURCES",
    "ExchangeEarningsDirectionResult",
    "materialize_registered_exchange_earnings_directions",
]
