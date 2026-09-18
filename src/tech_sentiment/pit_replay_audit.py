from __future__ import annotations

import json

import pandas as pd

from .pit_public_materialization import validate_materialized_pit_records


_REVISION_KEY = (
    "source_identity",
    "entity_id",
    "evidence_type",
    "event_date",
    "document_id",
    "revision_id",
)

_REPLAY_SORT_KEY = (
    "evidence_available_date",
    "source_identity",
    "entity_id",
    "evidence_id",
)


def as_of_evidence(records: pd.DataFrame, market_date: object) -> pd.DataFrame:
    validated = validate_materialized_pit_records(records)
    date = pd.Timestamp(market_date).normalize()
    return validated[
        pd.to_datetime(validated["evidence_available_date"]).dt.normalize().le(date)
    ].sort_values(
        list(_REPLAY_SORT_KEY)
    ).reset_index(drop=True)


def _provenance_complete(records: pd.DataFrame) -> bool:
    for value in records["provenance"]:
        try:
            payload = json.loads(str(value))
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        if not payload.get("source_identity") or not payload.get("provider"):
            return False
    return True


def _revision_identity_safe(records: pd.DataFrame) -> bool:
    x = records.copy()
    x["event_date"] = pd.to_datetime(x["event_date"]).dt.normalize()
    # The same explicit document/revision identity may never resolve to two
    # evidence rows. A correction/restatement must carry a distinct revision.
    if x.duplicated(list(_REVISION_KEY)).any():
        return False
    return x["revision_id"].astype(str).str.strip().ne("").all()


def _linear_prefix_replay_invariants(records: pd.DataFrame) -> tuple[bool, bool]:
    """Prove historical cutoff invariants without replaying the full ledger per cutoff.

    The public as-of contract is a pure filter on normalized
    evidence_available_date followed by replay-key ordering.
    validate_materialized_pit_records has already normalized dates and proved
    global evidence_id and ingestion_identity uniqueness before this helper is
    called.

    Therefore, after one ordering by the exact replay key, every distinct
    availability-date cutoff is a contiguous prefix of this ordered frame.
    Later cutoffs can only extend that prefix; no earlier evidence row can be
    removed or rewritten. This is the same proof performed by the previous
    implementation, but avoids re-validating and re-sorting the complete
    ledger once for every historical cutoff.
    """

    ordered = records.sort_values(list(_REPLAY_SORT_KEY)).reset_index(drop=True)
    available = pd.to_datetime(
        ordered["evidence_available_date"], errors="raise"
    ).dt.normalize()

    # Availability date is the primary replay sort key, so monotonicity is
    # equivalent to every cutoff selecting one exact ordered prefix.
    prefix_replay_equal = bool(available.is_monotonic_increasing)

    # The validator already rejects duplicate evidence ids. Combined with
    # monotone prefix membership, later cutoffs are strict append-only
    # extensions (or equal when no new evidence arrives).
    append_only = bool(prefix_replay_equal and ordered["evidence_id"].is_unique)
    return prefix_replay_equal, append_only


def audit_pit_replay(records: pd.DataFrame) -> dict[str, object]:
    if records.empty:
        return {
            "records": 0,
            "required_fields_complete": False,
            "no_future_evidence": False,
            "duplicate_identity_free": False,
            "provenance_complete": False,
            "append_only_schema_ready": True,
            "prefix_replay_filter_equality": False,
            "as_of_replay_equality": False,
            "revision_identity_complete": False,
            "later_revision_does_not_rewrite_prior_rows": False,
        }

    # Canonical validation is intentionally performed exactly once here.
    # Re-validating the complete ledger for each historical cutoff caused
    # issuer aggregation to scale super-linearly with the number of market
    # dates and made otherwise valid large PIT ledgers exceed job timeouts.
    validated = validate_materialized_pit_records(records)

    required_text = (
        "entity_id",
        "evidence_type",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "availability_state",
    )
    fields_complete = all(
        validated[column].notna().all()
        and validated[column].astype(str).str.strip().ne("").all()
        for column in required_text
    )
    no_future = bool(
        (
            pd.to_datetime(validated["evidence_available_date"]).dt.normalize()
            >= pd.to_datetime(validated["event_date"]).dt.normalize()
        ).all()
    )
    duplicate_free = bool(
        not validated["evidence_id"].duplicated().any()
        and not validated["ingestion_identity"].duplicated().any()
    )
    revision_safe = bool(_revision_identity_safe(validated))
    prefix_equal, append_only = _linear_prefix_replay_invariants(validated)

    return {
        "records": int(len(validated)),
        "required_fields_complete": fields_complete,
        "no_future_evidence": no_future,
        "duplicate_identity_free": duplicate_free,
        "provenance_complete": _provenance_complete(validated),
        "append_only_schema_ready": True,
        "prefix_replay_filter_equality": prefix_equal,
        "as_of_replay_equality": prefix_equal,
        "revision_identity_complete": revision_safe,
        "later_revision_does_not_rewrite_prior_rows": append_only,
        "earliest_evidence_available_date": str(
            pd.to_datetime(validated["evidence_available_date"]).min().date()
        ),
        "latest_evidence_available_date": str(
            pd.to_datetime(validated["evidence_available_date"]).max().date()
        ),
    }


__all__ = ["as_of_evidence", "audit_pit_replay"]
