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

    assert 'SHARD_COUNT: "12"' in text
    assert "max-parallel: 4" in text
    assert "timeout-minutes: 180" in text
    assert "shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]" in text
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
