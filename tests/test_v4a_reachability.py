from pathlib import Path

import pytest

from tech_sentiment.v4a_reachability import (
    REQUIREMENTS,
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
        assert item.historical_reconstruction_path
        assert item.output_paths
        assert item.coverage_metadata
        assert item.provenance_metadata
        assert item.pit_audit_path
        assert item.readiness_evaluator
        assert item.bundle_inclusion_path
        assert item.private_consumer_contract


def test_current_architecture_fails_closed_before_expensive_run():
    report = assess_reachability(Path("."))
    assert report["state"] == "FAIL_CLOSED"
    assert report["formal_long_run_allowed"] is False
    assert set(report["unreachable_items"]) == {
        "fundamental_pit",
        "earnings_pit",
        "valuation_pit",
        "major_event_pit",
        "major_negative_exclusion",
        "clean_forward_external_evidence",
    }
    with pytest.raises(RuntimeError, match="structurally unreachable"):
        assert_formal_run_reachable(Path("."))
