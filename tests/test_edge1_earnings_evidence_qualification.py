from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.apply_edge1_exchange_earnings_qualification import (
    CLASSIFIER_VERSION,
    CONTRACT_ID,
    SOURCES,
    _negative_events,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/edge1_earnings_evidence_qualification_authorization_v1.json"
WORKFLOW = ROOT / ".github/workflows/qualify-capital-inputs.yml"
FINALIZER = ROOT / "scripts/finalize_capital_pit_materialization.py"


def _direction(direction: str, *, source: str = "SSE_ANNOUNCEMENT_ARCHIVE") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "evidence_id": "issuer-earnings-exchange:test",
                "entity_id": "688001.SH",
                "evidence_type": "ISSUER_EARNINGS_DIRECTION",
                "event_date": "2026-01-05",
                "evidence_available_date": "2026-01-06",
                "source_identity": source,
                "provider": "SHANGHAI_STOCK_EXCHANGE",
                "document_id": "doc-1",
                "revision_id": "DOCUMENT_SHA256:" + "a" * 64,
                "provenance": json.dumps(
                    {
                        "source_identity": source,
                        "document_id": "doc-1",
                        "document_sha256": "a" * 64,
                        "availability_rule": "DATE_ONLY_NEXT_TRADE_DATE",
                    },
                    sort_keys=True,
                ),
                "ingestion_identity": "ingest-test",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(
                    {
                        "earnings_expectation_direction": direction,
                        "classifier_version": CLASSIFIER_VERSION,
                    },
                    sort_keys=True,
                ),
                "source_url_identity": "https://www.sse.com.cn/example.pdf",
            }
        ]
    )


def test_authorization_contract_freezes_exact_earnings_boundary() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["contract_id"] == CONTRACT_ID
    assert contract["status"] == "EXPLICIT_USER_AUTHORIZED_EVIDENCE_QUALIFICATION"
    frozen = contract["frozen_semantics"]
    assert set(frozen["input_source_identities"]) == SOURCES
    assert frozen["classifier_version"] == CLASSIFIER_VERSION
    assert frozen["official_document_body_required"] is True
    assert frozen["unknown_remains_unknown"] is True
    for key in (
        "numeric_threshold_used",
        "price_or_return_used",
        "coverage_threshold_changed",
        "classifier_tokens_changed",
        "universe_changed",
        "model_changed",
        "outcome_definition_changed",
    ):
        assert frozen[key] is False
    boundary = contract["scope_boundary"]
    assert boundary["reuse_exact_base_artifact_symbol_scope"] is True
    assert boundary["scope_builder_v2_688065_correction_integrated_in_this_run"] is False
    forbidden = contract["forbidden_actions"]
    assert forbidden["low_outcome_read_before_gate"] is True
    assert forbidden["high_outcome_read_before_gate"] is True
    assert forbidden["new_universe"] is True
    assert forbidden["production_change"] is True
    assert forbidden["trading_authority_change"] is True


def test_earnings_warning_mapping_is_deterministic_and_only_down() -> None:
    down = _negative_events(_direction("DOWN"))
    assert len(down) == 1
    row = down.iloc[0]
    assert row["evidence_type"] == "EARNINGS_WARNING"
    assert row["source_identity"] == "SSE_ANNOUNCEMENT_ARCHIVE"
    payload = json.loads(row["evidence_payload"])
    assert payload["major_event_direction"] == "NEGATIVE"
    assert payload["deterministic_mapping"] == (
        "ISSUER_EARNINGS_DIRECTION_DOWN_TO_EARNINGS_WARNING"
    )
    assert _negative_events(_direction("UP")).empty


def test_existing_manual_workflow_has_explicit_incremental_mode_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "edge1_earnings_qualification" in text
    assert "inputs.mode == 'edge1_earnings_qualification'" in text
    assert "inputs.mode != 'edge1_earnings_qualification'" in text
    assert "edge1-earnings-evidence-handoff-" in text
    assert "v4a-stage-bundles-v1" in text
    assert "pull_request:" not in text
    assert "\npush:" not in text
    assert "\nschedule:" not in text
    assert "\n  schedule:" not in text


def test_incremental_workflow_pins_exact_prior_qualified_envelope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    base = contract["base_qualified_artifact"]
    for value in (
        str(base["workflow_run_id"]),
        str(base["artifact_id"]),
        str(base["receipt_artifact_id"]),
        str(base["source_commit"]),
        str(base["artifact_digest"]),
        str(base["receipt_artifact_digest"]),
    ):
        assert value in text


def test_finalizer_carries_authorized_overlay_into_public_provenance() -> None:
    text = FINALIZER.read_text(encoding="utf-8")
    assert (
        '"edge1_earnings_qualification": '
        'pit_summary.get("edge1_earnings_qualification")'
    ) in text
