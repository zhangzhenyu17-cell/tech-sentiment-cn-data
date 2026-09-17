from __future__ import annotations

import json
import re

import pandas as pd

from .pit_public_materialization import _stable_hash, validate_materialized_pit_records


EARNINGS_SOURCE_ID = "DERIVED_PIT_ISSUER_EARNINGS_DIRECTION"
EARNINGS_PROVIDER = "DERIVED_VERSIONED_OFFICIAL_ISSUER_DISCLOSURES"
EARNINGS_CLASSIFIER_VERSION = "issuer-explicit-guidance-v1"

_UP_TOKENS = (
    "预增",
    "扭亏",
    "扭亏为盈",
    "续盈",
    "略增",
)
_DOWN_TOKENS = (
    "预减",
    "首亏",
    "续亏",
    "略减",
    "由盈转亏",
)


def classify_explicit_issuer_earnings_direction(value: object) -> str:
    """Classify only standardized issuer-disclosed guidance language.

    Unknown wording remains UNKNOWN.  No numeric threshold, market price, return,
    analyst consensus, or hindsight data is consulted.
    """

    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return "UNKNOWN"
    down = [token for token in _DOWN_TOKENS if token in text]
    up = [token for token in _UP_TOKENS if token in text]
    if down and up:
        return "UNKNOWN"
    if down:
        return "DOWN"
    if up:
        return "UP"
    return "UNKNOWN"


def derive_earnings_direction_evidence(issuer_records: pd.DataFrame) -> pd.DataFrame:
    """Create PIT direction evidence from versioned issuer forecast disclosures.

    The caller should pass the already-normalized CNINFO/SSE/SZSE issuer ledger.
    Records without explicit standardized direction remain absent from this
    derived evidence; source-window coverage is assessed separately.
    """

    required = {
        "entity_id",
        "evidence_type",
        "event_date",
        "evidence_available_date",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "title",
    }
    missing = required - set(issuer_records.columns)
    if missing:
        raise ValueError(f"issuer evidence missing columns: {sorted(missing)}")
    if issuer_records.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    candidates = issuer_records[
        issuer_records["evidence_type"].astype(str).isin(
            {"ISSUER_EARNINGS_FORECAST", "FINANCIAL_RESTATEMENT"}
        )
    ]
    for _, record in candidates.iterrows():
        text = str(record.get("title") or "")
        direction = classify_explicit_issuer_earnings_direction(text)
        if direction == "UNKNOWN":
            continue
        payload = {
            "earnings_expectation_direction": direction,
            "classification_basis": "EXPLICIT_ISSUER_GUIDANCE_TOKEN",
            "classifier_version": EARNINGS_CLASSIFIER_VERSION,
            "title": text,
            "source_document_id": str(record["document_id"]),
            "source_revision_id": str(record["revision_id"]),
        }
        provenance = {
            "derived_source_identity": EARNINGS_SOURCE_ID,
            "source_identity": str(record["source_identity"]),
            "source_provider": str(record["provider"]),
            "source_document_id": str(record["document_id"]),
            "source_revision_id": str(record["revision_id"]),
            "source_provenance": str(record["provenance"]),
            "classifier_version": EARNINGS_CLASSIFIER_VERSION,
            "price_or_return_used": False,
            "numeric_threshold_used": False,
        }
        observation = {
            "entity_id": str(record["entity_id"]),
            "document_id": str(record["document_id"]),
            "revision_id": str(record["revision_id"]),
            "available": str(pd.Timestamp(record["evidence_available_date"]).date()),
            "direction": direction,
        }
        rows.append(
            {
                "evidence_id": f"derived-earnings:{_stable_hash(observation)}",
                "entity_id": str(record["entity_id"]),
                "evidence_type": "ISSUER_EARNINGS_DIRECTION",
                "event_date": pd.Timestamp(record["event_date"]).normalize(),
                "evidence_available_date": pd.Timestamp(
                    record["evidence_available_date"]
                ).normalize(),
                "source_identity": EARNINGS_SOURCE_ID,
                "provider": EARNINGS_PROVIDER,
                "document_id": str(record["document_id"]),
                "revision_id": str(record["revision_id"]),
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(
                    {"observation": observation, "payload": payload}
                ),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "source_url_identity": str(record.get("source_url_identity") or ""),
            }
        )
    if not rows:
        return pd.DataFrame()
    return validate_materialized_pit_records(pd.DataFrame(rows))


__all__ = [
    "EARNINGS_SOURCE_ID",
    "EARNINGS_PROVIDER",
    "EARNINGS_CLASSIFIER_VERSION",
    "classify_explicit_issuer_earnings_direction",
    "derive_earnings_direction_evidence",
]
