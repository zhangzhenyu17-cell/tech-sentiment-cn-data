import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assert_v4a_stage_qualifiable.py"
_SPEC = importlib.util.spec_from_file_location("v4a_stage_guard_test_module", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
stage_blockers = _MODULE.stage_blockers


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _coverage(path: Path, statuses: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"query_status": statuses}).to_csv(path, index=False)


def test_capital_and_financing_guards_match_existing_readiness_rules(tmp_path: Path):
    capital = tmp_path / "capital"
    _write_json(
        capital / "qualification_summary.json",
        {
            "etf_readiness_state": "QUALIFIED_INPUT",
            "turnover_readiness_state": "QUALIFIED_INPUT",
        },
    )
    assert stage_blockers("capital", capital) == []
    _write_json(
        capital / "qualification_summary.json",
        {
            "etf_readiness_state": "PARTIAL_COVERAGE",
            "turnover_readiness_state": "QUALIFIED_INPUT",
        },
    )
    assert stage_blockers("capital", capital) == [
        "etf_readiness_state=PARTIAL_COVERAGE"
    ]

    financing = tmp_path / "financing"
    _write_json(
        financing / "financing_manifest.json",
        {
            "qualification_state": "CANONICAL_UNIT_QUALIFIED",
            "bilateral_coverage": 1.0,
        },
    )
    assert stage_blockers("financing", financing) == []
    _write_json(
        financing / "financing_manifest.json",
        {
            "qualification_state": "CANONICAL_UNIT_QUALIFIED",
            "bilateral_coverage": 0.99,
        },
    )
    assert stage_blockers("financing", financing) == [
        "bilateral_coverage=0.99"
    ]


def test_issuer_guard_blocks_partial_source_or_failed_symbol_query(tmp_path: Path):
    root = tmp_path / "issuer"
    _write_json(
        root / "pit_materialization_manifest.json",
        {
            "selected_sources": ["CNINFO_ANNOUNCEMENT_ARCHIVE"],
            "source_states": {
                "CNINFO_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
            },
            "failed_symbol_queries": 0,
        },
    )
    assert stage_blockers("issuer", root) == []

    _write_json(
        root / "pit_materialization_manifest.json",
        {
            "selected_sources": ["CNINFO_ANNOUNCEMENT_ARCHIVE"],
            "source_states": {
                "CNINFO_ANNOUNCEMENT_ARCHIVE": "PARTIAL_COVERAGE",
            },
            "failed_symbol_queries": 1,
        },
    )
    blockers = stage_blockers("issuer", root)
    assert "CNINFO_ANNOUNCEMENT_ARCHIVE=PARTIAL_COVERAGE" in blockers
    assert "failed_symbol_queries=1" in blockers


def test_fundamental_earnings_guard_only_requires_query_coverage(tmp_path: Path):
    root = tmp_path / "fundamental"
    _coverage(root / "filing_coverage.csv", ["COMPLETE_WINDOW", "COMPLETE_WINDOW"])
    _coverage(
        root / "earnings_direction_coverage.csv",
        ["COMPLETE_WINDOW", "COMPLETE_WINDOW"],
    )
    assert stage_blockers("fundamental_earnings", root) == []

    _coverage(root / "filing_coverage.csv", ["COMPLETE_WINDOW", "FAILED"])
    blockers = stage_blockers("fundamental_earnings", root)
    assert blockers == ["filings:non_complete_status=FAILED"]


def test_policy_guard_matches_existing_policy_readiness(tmp_path: Path):
    root = tmp_path / "policy"
    _write_json(
        root / "stage_manifest.json",
        {
            "policy_materialization": {
                "readiness_state": "QUALIFIED_INPUT",
                "source_coverage_complete": True,
            }
        },
    )
    assert stage_blockers("policy", root) == []

    _write_json(
        root / "stage_manifest.json",
        {
            "policy_materialization": {
                "readiness_state": "PARTIAL_COVERAGE",
                "source_coverage_complete": False,
            }
        },
    )
    blockers = stage_blockers("policy", root)
    assert "policy_readiness_state=PARTIAL_COVERAGE" in blockers
    assert "policy_source_coverage_complete=false" in blockers


def test_derived_guard_matches_existing_public_readiness_dependencies(tmp_path: Path):
    root = tmp_path / "derived"
    source_states = {
        "CNINFO_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
        "SSE_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
        "SZSE_ANNOUNCEMENT_ARCHIVE": "QUALIFIED_INPUT",
        "DERIVED_PIT_FUNDAMENTAL_TRENDS": "QUALIFIED_INPUT",
        "DERIVED_PIT_TRAILING_VALUATION": "QUALIFIED_INPUT",
        "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE": "QUALIFIED_INPUT",
    }
    audit = {
        "required_fields_complete": True,
        "no_future_evidence": True,
        "duplicate_identity_free": True,
        "provenance_complete": True,
        "prefix_replay_filter_equality": True,
        "as_of_replay_equality": True,
        "revision_identity_complete": True,
        "later_revision_does_not_rewrite_prior_rows": True,
    }
    _write_json(
        root / "derived_pit_materialization_manifest.json",
        {
            "source_states": source_states,
            "earnings_direction_readiness_state": "QUALIFIED_INPUT",
            "major_negative_event_exclusion_complete": True,
            "pit_audit": audit,
        },
    )
    assert stage_blockers("derived", root) == []

    broken = dict(source_states)
    broken["DERIVED_PIT_TRAILING_VALUATION"] = "PARTIAL_COVERAGE"
    _write_json(
        root / "derived_pit_materialization_manifest.json",
        {
            "source_states": broken,
            "earnings_direction_readiness_state": "QUALIFIED_INPUT",
            "major_negative_event_exclusion_complete": True,
            "pit_audit": audit,
        },
    )
    assert (
        "DERIVED_PIT_TRAILING_VALUATION=PARTIAL_COVERAGE"
        in stage_blockers("derived", root)
    )
