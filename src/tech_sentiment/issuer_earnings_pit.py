from __future__ import annotations

import json
import re

import pandas as pd

from .pit_public_materialization import _stable_hash, validate_materialized_pit_records


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

    Unknown wording remains UNKNOWN. No numeric threshold, market price, return,
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


def enrich_issuer_earnings_direction(issuer_records: pd.DataFrame) -> pd.DataFrame:
    """Attach explicit issuer earnings direction without creating a new source.

    The output preserves the registered CNINFO/SSE/SZSE source identity and the
    document/revision identity.  Only the evidence payload/provenance are
    augmented when the disclosure itself contains a standardized direction.
    This is field extraction from the same canonical document, not a new
    evidence source or eligibility rule.
    """

    required = {
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
    }
    missing = required - set(issuer_records.columns)
    if missing:
        raise ValueError(f"issuer evidence missing columns: {sorted(missing)}")
    if issuer_records.empty:
        return issuer_records.copy()

    out = issuer_records.copy()
    if "evidence_payload" not in out.columns:
        out["evidence_payload"] = ""

    for index, record in out.iterrows():
        if str(record["evidence_type"]) != "ISSUER_EARNINGS_FORECAST":
            continue
        title = str(record.get("title") or "")
        direction = classify_explicit_issuer_earnings_direction(title)
        if direction == "UNKNOWN":
            continue

        prior_payload: dict[str, object] = {}
        raw_payload = str(record.get("evidence_payload") or "").strip()
        if raw_payload:
            parsed = json.loads(raw_payload)
            if not isinstance(parsed, dict):
                raise ValueError("issuer evidence_payload must be a JSON object")
            prior_payload = dict(parsed)
        prior_payload["earnings_expectation_direction"] = direction
        prior_payload["earnings_direction_basis"] = "EXPLICIT_ISSUER_GUIDANCE_TOKEN"
        prior_payload["earnings_classifier_version"] = EARNINGS_CLASSIFIER_VERSION

        raw_provenance = json.loads(str(record["provenance"]))
        if not isinstance(raw_provenance, dict):
            raise ValueError("issuer provenance must be a JSON object")
        provenance = dict(raw_provenance)
        provenance["earnings_direction_extraction"] = {
            "basis": "EXPLICIT_ISSUER_GUIDANCE_TOKEN",
            "classifier_version": EARNINGS_CLASSIFIER_VERSION,
            "price_or_return_used": False,
            "numeric_threshold_used": False,
        }
        out.at[index, "evidence_payload"] = json.dumps(
            prior_payload, ensure_ascii=False, sort_keys=True
        )
        out.at[index, "provenance"] = json.dumps(
            provenance, ensure_ascii=False, sort_keys=True
        )
        out.at[index, "ingestion_identity"] = _stable_hash(
            {
                "source_identity": str(record["source_identity"]),
                "entity_id": str(record["entity_id"]),
                "document_id": str(record["document_id"]),
                "revision_id": str(record["revision_id"]),
                "evidence_type": str(record["evidence_type"]),
                "available": str(pd.Timestamp(record["evidence_available_date"]).date()),
                "payload": prior_payload,
            }
        )

    return validate_materialized_pit_records(out)


__all__ = [
    "EARNINGS_CLASSIFIER_VERSION",
    "classify_explicit_issuer_earnings_direction",
    "enrich_issuer_earnings_direction",
]
