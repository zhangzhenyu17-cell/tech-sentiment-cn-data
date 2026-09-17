import json

import pandas as pd

from tech_sentiment.major_negative_review import (
    CNINFO_SOURCE_ID,
    SSE_SOURCE_ID,
    SZSE_SOURCE_ID,
    build_major_negative_coverage_ledger,
    review_major_negative_events,
)
from tech_sentiment.official_policy_archive import POLICY_ENTITY_ID, POLICY_SOURCE_ID


def _coverage(source: str, entity: str, segment: str | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "source_identity": source,
        "entity_id": entity,
        "coverage_start": "2022-01-04",
        "coverage_end": "2026-09-18",
        "query_status": "COMPLETE_WINDOW",
    }
    if segment is not None:
        row["coverage_segment"] = segment
    return row


def test_wrong_exchange_is_not_required_and_nmpa_is_not_universal():
    scope = pd.DataFrame(
        [
            {"symbol": "600000", "market": "SH"},
            {"symbol": "300001", "market": "SZ"},
        ]
    )
    issuer = pd.DataFrame(
        [
            _coverage(CNINFO_SOURCE_ID, "600000.SH"),
            _coverage(SSE_SOURCE_ID, "600000.SH"),
            _coverage(CNINFO_SOURCE_ID, "300001.SZ"),
            _coverage(SZSE_SOURCE_ID, "300001.SZ"),
        ]
    )
    policy = pd.DataFrame(
        [
            _coverage(POLICY_SOURCE_ID, POLICY_ENTITY_ID, "A"),
            _coverage(POLICY_SOURCE_ID, POLICY_ENTITY_ID, "B"),
        ]
    )
    ledger = build_major_negative_coverage_ledger(
        frozen_scope=scope,
        issuer_coverage=issuer,
        policy_coverage=policy,
        start_date="2022-01-04",
        end_date="2026-09-18",
    )
    sh_sources = set(ledger[ledger["entity_id"] == "600000.SH"]["source_identity"])
    sz_sources = set(ledger[ledger["entity_id"] == "300001.SZ"]["source_identity"])
    assert sh_sources == {CNINFO_SOURCE_ID, SSE_SOURCE_ID, POLICY_SOURCE_ID}
    assert sz_sources == {CNINFO_SOURCE_ID, SZSE_SOURCE_ID, POLICY_SOURCE_ID}
    assert ledger["coverage_complete"].all()


def test_review_completeness_does_not_erase_detected_negative_events():
    ledger = pd.DataFrame(
        [
            {
                "entity_id": "600000.SH",
                "source_identity": CNINFO_SOURCE_ID,
                "applicable": True,
                "coverage_complete": True,
            },
            {
                "entity_id": "600000.SH",
                "source_identity": SSE_SOURCE_ID,
                "applicable": True,
                "coverage_complete": True,
            },
            {
                "entity_id": "600000.SH",
                "source_identity": POLICY_SOURCE_ID,
                "applicable": True,
                "coverage_complete": True,
            },
        ]
    )
    evidence = pd.DataFrame(
        [
            {
                "evidence_id": "neg-1",
                "entity_id": "600000.SH",
                "evidence_type": "MAJOR_NEGATIVE_EVENT",
            }
        ]
    )
    review, summary = review_major_negative_events(
        coverage_ledger=ledger, evidence_records=evidence
    )
    assert summary["major_negative_event_exclusion_complete"] is True
    assert review.loc[0, "explicit_negative_event_count"] == 1
    assert review.loc[0, "exclusion_clear"] == False
    assert json.loads(review.loc[0, "explicit_negative_evidence_ids"]) == ["neg-1"]


def test_missing_applicable_source_fails_closed():
    ledger = pd.DataFrame(
        [
            {
                "entity_id": "600000.SH",
                "source_identity": CNINFO_SOURCE_ID,
                "applicable": True,
                "coverage_complete": False,
            }
        ]
    )
    review, summary = review_major_negative_events(
        coverage_ledger=ledger, evidence_records=pd.DataFrame()
    )
    assert summary["major_negative_event_exclusion_complete"] is False
    assert review.loc[0, "review_complete"] == False
    assert review.loc[0, "exclusion_clear"] == False
