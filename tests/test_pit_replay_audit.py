import json

import pandas as pd

import tech_sentiment.pit_replay_audit as pit_replay_audit
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records
from tech_sentiment.pit_replay_audit import as_of_evidence, audit_pit_replay


def _row(evidence_id: str, available: str, revision: str, payload: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "entity_id": "600000.SH",
        "evidence_type": "REVENUE_TREND",
        "event_date": "2024-12-31",
        "evidence_available_date": available,
        "source_identity": "DERIVED_PIT_FUNDAMENTAL_TRENDS",
        "provider": "DERIVED_VERSIONED_OFFICIAL_FILINGS",
        "document_id": revision,
        "revision_id": revision,
        "provenance": json.dumps(
            {
                "source_identity": "DERIVED_PIT_FUNDAMENTAL_TRENDS",
                "provider": "DERIVED_VERSIONED_OFFICIAL_FILINGS",
            }
        ),
        "ingestion_identity": f"ing-{evidence_id}",
        "availability_state": "HISTORICAL_RECONSTRUCTABLE",
        "evidence_payload": payload,
    }


def _slow_reference_replay_invariants(records: pd.DataFrame) -> tuple[bool, bool]:
    """Reference the pre-optimization cutoff-by-cutoff replay semantics."""

    validated = validate_materialized_pit_records(records)
    ordered = validated.sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"]
    ).reset_index(drop=True)
    cutoffs = (
        pd.to_datetime(ordered["evidence_available_date"])
        .dt.normalize()
        .drop_duplicates()
    )

    prefix_equal = True
    for cutoff in cutoffs:
        expected = ordered[
            pd.to_datetime(ordered["evidence_available_date"])
            .dt.normalize()
            .le(cutoff)
        ].reset_index(drop=True)
        replay = as_of_evidence(ordered, cutoff)
        columns = list(ordered.columns)
        if not expected[columns].astype(str).equals(replay[columns].astype(str)):
            prefix_equal = False
            break

    append_only = True
    prior_ids: set[str] = set()
    for cutoff in cutoffs:
        replay = as_of_evidence(ordered, cutoff)
        ids = set(replay["evidence_id"].astype(str))
        if not prior_ids.issubset(ids):
            append_only = False
            break
        prior_ids = ids

    return prefix_equal, append_only


def test_as_of_replay_keeps_original_revision_after_later_restatement_arrives():
    records = pd.DataFrame(
        [
            _row("orig", "2025-04-20", "ORIGINAL", "{\"value\": 1}"),
            _row("restated", "2025-06-01", "RESTATEMENT", "{\"value\": 2}"),
        ]
    )
    april = as_of_evidence(records, "2025-05-01")
    june = as_of_evidence(records, "2025-06-10")
    assert april["evidence_id"].tolist() == ["orig"]
    assert set(june["evidence_id"]) == {"orig", "restated"}
    audit = audit_pit_replay(records)
    assert audit["prefix_replay_filter_equality"] is True
    assert audit["as_of_replay_equality"] is True
    assert audit["later_revision_does_not_rewrite_prior_rows"] is True
    assert audit["revision_identity_complete"] is True


def test_linear_audit_matches_previous_cutoff_by_cutoff_semantics():
    rows: list[dict[str, object]] = []
    for idx, available in enumerate(
        [
            "2025-01-10",
            "2025-01-10",
            "2025-02-03",
            "2025-03-15",
            "2025-03-15",
            "2025-06-01",
        ]
    ):
        row = _row(
            f"e-{idx}",
            available,
            f"DOC-{idx}",
            json.dumps({"value": idx}),
        )
        if idx % 2:
            row["entity_id"] = "000001.SZ"
            row["source_identity"] = "CNINFO_ANNOUNCEMENT_ARCHIVE"
            row["provider"] = "CNINFO"
            row["provenance"] = json.dumps(
                {
                    "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                    "provider": "CNINFO",
                }
            )
        rows.append(row)

    records = pd.DataFrame(rows).sample(frac=1.0, random_state=17).reset_index(drop=True)
    expected_prefix, expected_append_only = _slow_reference_replay_invariants(records)
    audit = audit_pit_replay(records)

    assert audit["prefix_replay_filter_equality"] is expected_prefix
    assert audit["as_of_replay_equality"] is expected_prefix
    assert audit["later_revision_does_not_rewrite_prior_rows"] is expected_append_only


def test_audit_validates_large_many_cutoff_ledger_once(monkeypatch):
    base = pd.Timestamp("2025-01-01")
    records = pd.DataFrame(
        [
            _row(
                f"e-{idx}",
                str((base + pd.Timedelta(days=idx)).date()),
                f"DOC-{idx}",
                json.dumps({"value": idx}),
            )
            for idx in range(256)
        ]
    )

    original_validate = pit_replay_audit.validate_materialized_pit_records
    calls = {"count": 0}

    def counted_validate(frame: pd.DataFrame) -> pd.DataFrame:
        calls["count"] += 1
        return original_validate(frame)

    def unexpected_full_replay(*args, **kwargs):
        raise AssertionError("audit_pit_replay must not full-replay the ledger per cutoff")

    monkeypatch.setattr(
        pit_replay_audit,
        "validate_materialized_pit_records",
        counted_validate,
    )
    monkeypatch.setattr(pit_replay_audit, "as_of_evidence", unexpected_full_replay)

    audit = pit_replay_audit.audit_pit_replay(records)

    assert calls["count"] == 1
    assert audit["prefix_replay_filter_equality"] is True
    assert audit["later_revision_does_not_rewrite_prior_rows"] is True
