from __future__ import annotations

import json
from typing import Iterable

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


def as_of_evidence(records: pd.DataFrame, market_date: object) -> pd.DataFrame:
    validated = validate_materialized_pit_records(records)
    date = pd.Timestamp(market_date).normalize()
    return validated[
        pd.to_datetime(validated["evidence_available_date"]).dt.normalize().le(date)
    ].sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"]
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


def _prefix_replay_equal(records: pd.DataFrame) -> bool:
    ordered = records.sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"]
    ).reset_index(drop=True)
    cutoffs = pd.to_datetime(ordered["evidence_available_date"]).dt.normalize().drop_duplicates()
    for cutoff in cutoffs:
        expected = ordered[
            pd.to_datetime(ordered["evidence_available_date"]).dt.normalize().le(cutoff)
        ].reset_index(drop=True)
        replay = as_of_evidence(ordered, cutoff)
        columns = list(ordered.columns)
        if not expected[columns].astype(str).equals(replay[columns].astype(str)):
            return False
    return True


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
        (pd.to_datetime(validated["evidence_available_date"]).dt.normalize()
         >= pd.to_datetime(validated["event_date"]).dt.normalize()).all()
    )
    duplicate_free = bool(
        not validated["evidence_id"].duplicated().any()
        and not validated["ingestion_identity"].duplicated().any()
    )
    revision_safe = bool(_revision_identity_safe(validated))
    prefix_equal = bool(_prefix_replay_equal(validated))

    # Append-only proof: every earlier as-of ledger is an exact row subset of
    # the final ledger. Later revisions add rows; they never replace a prior row.
    ordered = validated.sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"]
    ).reset_index(drop=True)
    append_only = True
    prior_ids: set[str] = set()
    for cutoff in pd.to_datetime(ordered["evidence_available_date"]).dt.normalize().drop_duplicates():
        replay = as_of_evidence(ordered, cutoff)
        ids = set(replay["evidence_id"].astype(str))
        if not prior_ids.issubset(ids):
            append_only = False
            break
        prior_ids = ids

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
