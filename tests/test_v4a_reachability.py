import json
from pathlib import Path

import pytest

from tech_sentiment.v4a_reachability import (
    PRIVATE_HANDOFF_PATH,
    REQUIREMENTS,
    _private_handoff_blockers,
    assess_reachability,
    assert_formal_run_reachable,
)


def test_reachability_contract_covers_all_frozen_v4a_items():
    assert {item.item for item in REQUIREMENTS} == {
        "588000_long_flow",
        "sse_szse_a_shares_turnover",
        "financing",
        "fundamental_pit",
        "earnings_pit",
        "valuation_pit",
        "major_event_pit",
        "major_negative_exclusion",
        "clean_forward_external_evidence",
    }
    for item in REQUIREMENTS:
        assert item.producer_paths
        assert item.test_paths
        assert item.historical_reconstruction_path
        assert item.output_paths
        assert item.coverage_metadata
        assert item.provenance_metadata
        assert item.pit_audit_path
        assert item.readiness_evaluator
        assert item.bundle_inclusion_path
        assert item.private_consumer_contract == "V4A_PRIVATE_QUALIFICATION_HANDOFF_V2"


def test_all_public_rails_are_structurally_reachable_before_private_activation():
    report = assess_reachability(Path("."))
    handoff = json.loads(Path(PRIVATE_HANDOFF_PATH).read_text(encoding="utf-8"))
    if handoff["activation_state"] == "ACTIVE":
        assert report["state"] == "REACHABLE"
        assert report["formal_long_run_allowed"] is True
        assert report["unreachable_items"] == []
        assert_formal_run_reachable(Path("."))
    else:
        assert report["state"] == "FAIL_CLOSED"
        assert report["formal_long_run_allowed"] is False
        assert report["unreachable_items"] == ["clean_forward_external_evidence"]
        clean = next(
            row
            for row in report["requirements"]
            if row["item"] == "clean_forward_external_evidence"
        )
        assert "private_v4a2_verifier_not_activated" in clean["blockers"]
        with pytest.raises(RuntimeError, match="structurally unreachable"):
            assert_formal_run_reachable(Path("."))


def test_public_rails_have_no_remaining_hard_coded_materialization_blockers():
    report = assess_reachability(Path("."))
    for row in report["requirements"]:
        if row["item"] == "clean_forward_external_evidence":
            continue
        assert row["structurally_reachable"] is True, (row["item"], row["blockers"])
        assert not any("NOT_MATERIALIZED" in str(value) for value in row["blockers"])


def test_active_private_handoff_pins_v2_verifier_and_safety_boundaries():
    handoff = json.loads(Path(PRIVATE_HANDOFF_PATH).read_text(encoding="utf-8"))
    assert handoff["activation_state"] == "ACTIVE"
    assert handoff["private_verifier_contract_id"] == "v4a_artifact_intake_contract_v2"
    assert handoff["private_verifier_merge_sha"] == "b953b9c302467f40dbdb51d421b06ed1f78904b1"
    assert len(handoff["private_verifier_merge_sha"]) == 40
    assert handoff["public_grants_historical_qualification"] is False
    assert handoff["formal_public_long_run_allowed_before_activation"] is False
    assert handoff["formal_public_long_run_allowed_after_activation"] is True
    assert handoff["forward_outcome_read_required"] is False
    assert handoff["parameter_search_required"] is False
    assert handoff["production_or_trading_change_required"] is False
    assert _private_handoff_blockers(Path(".")) == []


def test_private_handoff_invalid_sha_or_boundary_fails_closed(tmp_path):
    path = tmp_path / PRIVATE_HANDOFF_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    handoff = json.loads(Path(PRIVATE_HANDOFF_PATH).read_text(encoding="utf-8"))
    handoff["private_verifier_merge_sha"] = "not-a-commit"
    handoff["forward_outcome_read_required"] = True
    path.write_text(json.dumps(handoff), encoding="utf-8")
    blockers = _private_handoff_blockers(tmp_path)
    assert "private_v4a2_verifier_merge_sha_invalid" in blockers
    assert "forward_outcome_boundary_invalid" in blockers
