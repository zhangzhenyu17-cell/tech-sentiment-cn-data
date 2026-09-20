from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .pit_public_materialization import validate_materialized_pit_records
from .pit_replay_audit import audit_pit_replay
from .v4a_stage_artifact import file_sha256, verify_stage_receipt


REVISION_KEY = (
    "source_identity",
    "entity_id",
    "evidence_type",
    "event_date",
    "document_id",
    "revision_id",
)
_VOLATILE_DUPLICATE_FIELDS = {
    "evidence_id",
    "evidence_available_date",
    "ingestion_identity",
}
_AUDIT_REQUIRED = (
    "required_fields_complete",
    "no_future_evidence",
    "duplicate_identity_free",
    "provenance_complete",
    "prefix_replay_filter_equality",
    "as_of_replay_equality",
    "revision_identity_complete",
    "later_revision_does_not_rewrite_prior_rows",
)


def canonicalize_fundamental_state_revision_identity(
    records: pd.DataFrame,
) -> tuple[pd.DataFrame, tuple[str, ...], int]:
    """Drop only byte-semantic duplicate state snapshots for one explicit revision.

    A derived FUNDAMENTAL_STATE revision is defined by the existing PIT replay
    revision key. If an unrelated filing cutoff causes the exact same revision,
    payload, provenance, provider, availability state, and auxiliary fields to
    be emitted again at a later availability date, retain only the first date.

    If any non-volatile field differs within one revision identity, fail closed:
    that is a real identity conflict and must never be silently deduplicated.
    """

    if records.empty:
        return records.copy(), (), 0
    validated = validate_materialized_pit_records(records)
    x = validated.copy()
    x["event_date"] = pd.to_datetime(x["event_date"], errors="raise").dt.normalize()
    x["evidence_available_date"] = pd.to_datetime(
        x["evidence_available_date"], errors="raise"
    ).dt.normalize()

    duplicate_mask = x.duplicated(list(REVISION_KEY), keep=False)
    if not duplicate_mask.any():
        return validated.reset_index(drop=True), (), 0

    duplicate_rows = x.loc[duplicate_mask].copy()
    dropped: list[str] = []
    duplicate_group_count = 0
    for _, group in duplicate_rows.groupby(list(REVISION_KEY), dropna=False, sort=True):
        duplicate_group_count += 1
        compare_columns = [
            column
            for column in group.columns
            if column not in _VOLATILE_DUPLICATE_FIELDS
        ]
        for column in compare_columns:
            values = group[column].fillna("<NA>").astype(str)
            if values.nunique(dropna=False) != 1:
                raise ValueError(
                    "derived fundamental revision identity conflict: "
                    f"column={column}"
                )

        ordered = group.sort_values(
            ["evidence_available_date", "evidence_id"],
            kind="mergesort",
        )
        dropped.extend(ordered.iloc[1:]["evidence_id"].astype(str).tolist())

    if not dropped:
        return validated.reset_index(drop=True), (), 0
    dropped_rows = validated[
        validated["evidence_id"].astype(str).isin(set(dropped))
    ]
    if not dropped_rows["availability_state"].astype(str).eq("DATA_INSUFFICIENT").all():
        raise ValueError(
            "automatic Derived revision canonicalization may only remove "
            "redundant DATA_INSUFFICIENT snapshots"
        )
    out = validated[~validated["evidence_id"].astype(str).isin(set(dropped))].copy()
    out = out.sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    return (
        validate_materialized_pit_records(out),
        tuple(sorted(dropped)),
        duplicate_group_count,
    )


def _rewrite_extended_without_evidence_ids(
    path: Path,
    *,
    dropped_ids: set[str],
) -> None:
    if not dropped_ids:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    first = True
    dropped_seen: set[str] = set()
    for chunk in pd.read_csv(path, dtype=str, chunksize=25_000):
        if "evidence_id" not in chunk.columns:
            raise ValueError("extended PIT evidence missing evidence_id")
        mask = chunk["evidence_id"].astype(str).isin(dropped_ids)
        dropped_seen.update(chunk.loc[mask, "evidence_id"].astype(str))
        kept = chunk.loc[~mask]
        kept.to_csv(tmp, index=False, mode="w" if first else "a", header=first)
        first = False
    if dropped_seen != dropped_ids:
        missing = sorted(dropped_ids - dropped_seen)
        raise ValueError(
            "redundant fundamental revisions missing from extended ledger: "
            + ",".join(missing[:10])
        )
    tmp.replace(path)


def _read_audit_frame(path: Path) -> pd.DataFrame:
    columns = (
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
    )
    return pd.read_csv(path, usecols=list(columns), dtype=str)


def repair_completed_derived_stage(
    stage_root: str | Path,
    *,
    expected_source_commit: str,
    start_date: str,
    end_date: str,
    expected_receipt_sha256: str,
    recovery_run_id: int,
    recovery_artifact_id: int,
    recovery_artifact_digest: str,
    new_source_commit: str,
) -> dict[str, object]:
    root = Path(stage_root)
    receipt = root / "receipt.json"
    if file_sha256(receipt) != expected_receipt_sha256:
        raise ValueError("Derived recovery receipt SHA256 mismatch")
    verify_stage_receipt(
        root=root,
        receipt_path=receipt,
        source_commit=expected_source_commit,
        stage_kind="derived",
        start_date=start_date,
        end_date=end_date,
    )

    pit_root = root / "pit_evidence_materialization"
    manifest_path = pit_root / "derived_pit_materialization_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Derived recovery manifest must be an object")
    if manifest.get("source_commit") != expected_source_commit:
        raise ValueError("Derived recovery source commit mismatch")
    old_audit = manifest.get("pit_audit")
    if not isinstance(old_audit, dict):
        raise ValueError("Derived recovery PIT audit missing")
    for field in _AUDIT_REQUIRED:
        expected = field != "revision_identity_complete"
        if old_audit.get(field) is not expected:
            raise ValueError(
                "Derived recovery has unexpected pre-repair audit state: "
                f"{field}={old_audit.get(field)!r}"
            )

    fundamental_path = pit_root / "fundamental_state_evidence.csv"
    fundamental = pd.read_csv(fundamental_path)
    canonical, dropped, duplicate_groups = canonicalize_fundamental_state_revision_identity(fundamental)
    if not dropped:
        raise ValueError("Derived recovery expected redundant fundamental revisions")
    canonical.to_csv(fundamental_path, index=False)

    extended_path = pit_root / "pit_evidence_extended.csv"
    _rewrite_extended_without_evidence_ids(
        extended_path,
        dropped_ids=set(dropped),
    )
    audit = audit_pit_replay(_read_audit_frame(extended_path))
    failed = [field for field in _AUDIT_REQUIRED if audit.get(field) is not True]
    if failed:
        raise ValueError(
            "Derived repaired PIT audit still blocked: " + ",".join(failed)
        )

    states = canonical["availability_state"].astype(str)
    qualified = int(states.eq("HISTORICAL_RECONSTRUCTABLE").sum())
    insufficient = int(states.eq("DATA_INSUFFICIENT").sum())
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    available = pd.to_datetime(
        canonical["evidence_available_date"], errors="raise"
    ).dt.normalize()
    target = canonical[available.between(start, end)].copy()
    target_states = target["availability_state"].astype(str)

    fundamental_contract = manifest.get("fundamental_state_contract")
    if not isinstance(fundamental_contract, dict):
        raise ValueError("Derived recovery fundamental contract summary missing")
    fundamental_contract["state_records"] = int(len(canonical))
    fundamental_contract["qualified_state_records"] = qualified
    fundamental_contract["data_insufficient_records"] = insufficient

    fundamental_coverage = manifest.get("fundamental_coverage")
    if not isinstance(fundamental_coverage, dict):
        raise ValueError("Derived recovery fundamental coverage summary missing")
    fundamental_coverage["target_records"] = int(len(target))
    fundamental_coverage["qualified_records"] = int(
        target_states.eq("HISTORICAL_RECONSTRUCTABLE").sum()
    )
    fundamental_coverage["data_insufficient_records"] = int(
        target_states.eq("DATA_INSUFFICIENT").sum()
    )

    manifest["source_commit"] = str(new_source_commit)
    manifest["pit_audit"] = audit
    manifest["fundamental_state_contract"] = fundamental_contract
    manifest["fundamental_coverage"] = fundamental_coverage
    manifest["revision_identity_canonicalization"] = {
        "schema_version": "v4a-derived-revision-identity-canonicalization-v1",
        "recovery_run_id": int(recovery_run_id),
        "recovery_artifact_id": int(recovery_artifact_id),
        "recovery_artifact_digest": str(recovery_artifact_digest),
        "source_stage_receipt_sha256": expected_receipt_sha256,
        "duplicate_revision_groups": int(duplicate_groups),
        "dropped_redundant_rows": int(len(dropped)),
        "new_source_commit": str(new_source_commit),
        "retention_rule": "EARLIEST_EVIDENCE_AVAILABLE_DATE_FOR_IDENTICAL_DATA_INSUFFICIENT_REVISION_PAYLOAD",
        "conflicting_revision_payloads_allowed": False,
        "qualified_state_rows_removed": 0,
        "evidence_source_eligibility_changed": False,
        "pit_no_lookahead_semantics_changed": False,
        "qualification_threshold_changed": False,
        "future_outcomes_used": False,
        "research_run": False,
        "production_authority_changed": False,
        "trading_authority_changed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    receipt.unlink()
    return {
        "recovery_run_id": int(recovery_run_id),
        "recovery_artifact_id": int(recovery_artifact_id),
        "dropped_redundant_rows": int(len(dropped)),
        "pit_audit": audit,
        "qualified_state_rows": qualified,
        "data_insufficient_state_rows": insufficient,
    }


__all__ = [
    "REVISION_KEY",
    "canonicalize_fundamental_state_revision_identity",
    "repair_completed_derived_stage",
]
