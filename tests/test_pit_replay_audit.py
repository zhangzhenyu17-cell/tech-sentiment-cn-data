import json

import pandas as pd

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
