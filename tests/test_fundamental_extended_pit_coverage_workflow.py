from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/fundamental-extended-pit-coverage.yml"
RUN_PLAN = ROOT / "docs/fundamental_extended_pit_historical_coverage_run_plan_2026-09-22.md"


def test_workflow_is_manual_only_and_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert trigger not in text

    assert 'SHARD_COUNT: "48"' in text
    assert "max-parallel: 4" in text
    # Live filing retrieval can exceed 30 minutes even for the two-symbol preflight.
    # Keep both preflight and shard materialization below GitHub's six-hour ceiling
    # without weakening the frozen scope or PIT contract.
    assert text.count("timeout-minutes: 360") >= 2
    assert "shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47]" in text
    assert "fail-fast: false" in text


def test_workflow_has_three_layer_preflight_and_recovery_controls() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "Audit public tree" in text
    assert "Targeted contract tests" in text
    assert "Build exact frozen 193-symbol scope" in text
    assert "Build real trading calendar including warmup" in text
    assert "Representative live materialization" in text
    assert "Validate representative execution classes" in text

    assert "actions/cache/restore@v4" in text
    assert "actions/cache/save@v4" in text
    assert "refs/heads/v4a/fundamental-resume-295710" in text
    assert "fail-on-cache-miss: true" in text
    assert "LEGACY_QUERY_PRIMARY_COMMIT" in text
    assert "LEGACY_QUERY_SECONDARY_COMMIT" in text
    assert "--legacy-checkpoint-dir .cache/fundamental_extended_pit_legacy/filings" in text
    assert "--legacy-query-checkpoint-source-commit" in text
    assert "--max-financial-documents-per-symbol 1" in text
    assert "always() && steps.materialize.outcome != 'skipped'" in text
    assert "--hard-failure-circuit-breaker-threshold 3" in text
    assert "--fail-on-hard-errors" in text
    assert "fundamental-extended-pit-coverage-audit" in text


def test_workflow_freezes_existing_v4a_window_and_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    plan = RUN_PLAN.read_text(encoding="utf-8")

    assert 'TARGET_START_DATE: "2022-01-04"' in text
    assert 'END_DATE: "2026-09-17"' in text
    assert "manifest[\"symbols\"] == 193" in text
    assert "capital-pit-frozen-universe-v2" in text

    assert "outcome blind" in plan.lower()
    assert "193" in plan
    assert "688065" in plan
    assert "2022-01-04" in plan
    assert "2026-09-17" in plan
    assert "evidence_qualification_changed=false" in plan


def test_workflow_decomposition_matches_observed_timeout_lessons() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'SHARD_COUNT: "48"' in text
    assert "max-parallel: 4" in text
    assert "legacy_shard = unit % int(bridge[\"source_shard_count\"])" in text
    assert "exact_cache_keys" in text
    assert "executed_symbol_queries\"] == 0" in text
    assert "reused_legacy_symbol_queries\"] == 2" in text
    assert "max_financial_documents_per_symbol\"] == 1" in text


def test_workflow_addresses_legacy_v4a_double_filings_layout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "--legacy-checkpoint-dir .cache/"
        "fundamental_extended_pit_preflight_legacy/filings"
    ) in text
    assert (
        "--legacy-checkpoint-dir .cache/"
        "fundamental_extended_pit_legacy/filings"
    ) in text
    assert "test -d .cache/fundamental_extended_pit_preflight_legacy/filings" in text
    assert "test -d .cache/fundamental_extended_pit_legacy/filings" in text
    assert "find .cache/fundamental_extended_pit_preflight_legacy/filings" in text
    assert "find .cache/fundamental_extended_pit_legacy/filings" in text


def test_shared_input_artifact_layout_is_gated_before_matrix_fanout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "shared_inputs_gate:" in text
    assert "needs: shared_inputs_gate" in text
    assert "Validate downloaded shared-input artifact layout" in text
    assert "stage/shared-download/shared/pit_symbol_scope/capital_pit_symbols.csv" in text
    assert "stage/shared-download/shared/pit_symbol_scope/capital_pit_symbol_scope.json" in text
    assert "stage/shared-download/shared/calendar/trading_calendar.csv" in text
    assert "stage/shared-download/shared/calendar/trading_calendar_manifest.json" in text
    assert "stage/shared-download/preflight/output/extended_filing_summary.json" in text
    assert "stage/shared-download/stage/shared/" not in text
    assert "needs: [preflight, shared_inputs_gate, materialize]" in text
    assert "needs.shared_inputs_gate.result == 'success'" in text
