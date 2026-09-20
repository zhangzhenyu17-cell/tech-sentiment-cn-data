import json

import pandas as pd
import pytest

from tech_sentiment.derived_revision_identity import (
    canonicalize_fundamental_state_revision_identity,
)
from tech_sentiment.pit_replay_audit import audit_pit_replay


def _row(
    *,
    evidence_id: str,
    available: str,
    ingestion: str,
    availability_state: str = "DATA_INSUFFICIENT",
    payload: str = "same",
):
    provenance = json.dumps(
        {"source_identity": "DERIVED_PIT_FUNDAMENTAL_TRENDS", "provider": "DERIVED"}
    )
    return {
        "evidence_id": evidence_id,
        "entity_id": "600000.SH",
        "evidence_type": "FUNDAMENTAL_STATE",
        "event_date": "2025-09-30",
        "evidence_available_date": available,
        "source_identity": "DERIVED_PIT_FUNDAMENTAL_TRENDS",
        "provider": "DERIVED",
        "document_id": "FUNDAMENTAL_STATE:600000.SH:2025-09-30:abc",
        "revision_id": "FUNDAMENTAL_PIT_STATE_CONTRACT_V1:abc",
        "provenance": provenance,
        "ingestion_identity": ingestion,
        "availability_state": availability_state,
        "evidence_payload": json.dumps({"payload": payload}),
        "source_url_identity": "DERIVED_FROM_VERSIONED_OFFICIAL_FILINGS",
    }


def test_redundant_data_insufficient_revision_keeps_earliest_snapshot_and_repairs_audit():
    records = pd.DataFrame(
        [
            _row(evidence_id="later", available="2025-11-25", ingestion="i-later"),
            _row(evidence_id="earlier", available="2025-10-28", ingestion="i-earlier"),
        ]
    )
    before = audit_pit_replay(records)
    assert before["revision_identity_complete"] is False

    canonical, dropped, groups = canonicalize_fundamental_state_revision_identity(records)

    assert groups == 1
    assert dropped == ("later",)
    assert list(canonical["evidence_id"]) == ["earlier"]
    after = audit_pit_replay(canonical)
    assert after["revision_identity_complete"] is True
    assert after["later_revision_does_not_rewrite_prior_rows"] is True


def test_revision_canonicalization_fails_closed_on_conflicting_payload():
    records = pd.DataFrame(
        [
            _row(
                evidence_id="a",
                available="2025-10-28",
                ingestion="i-a",
                payload="one",
            ),
            _row(
                evidence_id="b",
                available="2025-11-25",
                ingestion="i-b",
                payload="two",
            ),
        ]
    )
    with pytest.raises(ValueError, match="revision identity conflict"):
        canonicalize_fundamental_state_revision_identity(records)


def test_revision_canonicalization_never_auto_removes_qualified_evidence():
    records = pd.DataFrame(
        [
            _row(
                evidence_id="a",
                available="2025-10-28",
                ingestion="i-a",
                availability_state="HISTORICAL_RECONSTRUCTABLE",
            ),
            _row(
                evidence_id="b",
                available="2025-11-25",
                ingestion="i-b",
                availability_state="HISTORICAL_RECONSTRUCTABLE",
            ),
        ]
    )
    with pytest.raises(ValueError, match="DATA_INSUFFICIENT"):
        canonicalize_fundamental_state_revision_identity(records)
