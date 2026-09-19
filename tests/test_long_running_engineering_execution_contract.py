from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "reference" / "long_running_engineering_execution_contract_v1.json"
PROTOCOL_PATH = ROOT / "docs" / "long_running_engineering_execution_protocol.md"
RUN_PLAN_PATH = ROOT / "docs" / "templates" / "long_running_run_plan.md"
FUNDAMENTAL_WORKFLOW = ROOT / ".github" / "workflows" / "v4a-fundamental-earnings.yml"


def _contract() -> dict[str, object]:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_long_running_engineering_contract_is_frozen_and_boundary_preserving() -> None:
    payload = _contract()
    assert payload["schema_version"] == "long-running-engineering-execution-contract-v1"
    assert payload["status"] == "FROZEN_ENGINEERING_DEFAULT"

    scope = payload["scope"]
    assert scope["long_running_github_actions"] is True
    assert scope["public_data_materialization"] is True
    assert scope["engineering_repairs"] is True
    for key in (
        "changes_evidence_eligibility",
        "changes_pit_no_lookahead",
        "changes_model_threshold_signal",
        "changes_production_or_trading_authority",
    ):
        assert scope[key] is False

    defaults = payload["defaults"]
    for key in (
        "manual_only",
        "automatic_trigger_requires_explicit_allowlist",
        "deterministic_partitioning",
        "bounded_parallelism",
        "timeout_is_per_job_safety_ceiling",
        "diagnostics_before_qualification_gate",
        "durable_progress_before_qualification_gate",
        "immutable_unit_publish_after_gate",
        "completed_unit_reuse_precedes_recomputation",
        "query_cache_independent_from_document_progress",
        "actual_cli_entrypoint_regression_test_required",
        "cache_visibility_scope_must_be_preflighted",
        "inline_restore_resolver_execution_test_required",
        "resume_effectiveness_requires_runtime_counters",
        "post_success_producer_identity_freeze_required",
        "stage_success_distinct_from_pipeline_qualification",
    ):
        assert defaults[key] is True


def test_restore_precedence_and_compatibility_bridge_are_explicit() -> None:
    payload = _contract()
    assert payload["restore_precedence"] == [
        "IMMUTABLE_COMPLETED_WORK_UNIT",
        "CURRENT_SEMANTIC_DURABLE_PROGRESS",
        "EXACT_AUDITED_COMPATIBILITY_BRIDGE",
        "LEGACY_QUERY_INDEX_AND_DOCUMENT_CACHE",
        "PROVIDER_RECOMPUTATION",
    ]

    bridge = payload["compatibility_bridge"]
    for key in (
        "exceptional_only",
        "source_run_id_required",
        "source_run_attempt_required",
        "source_branch_or_ref_required",
        "cache_visibility_relationship_required",
        "wrong_ref_fail_fast_when_scope_required",
        "source_commit_required",
        "old_semantic_fingerprint_required",
        "exact_window_required",
        "exact_cache_or_bundle_identity_required",
        "compatibility_reason_required",
        "unchanged_invariants_required",
        "fail_closed_on_mismatch",
        "removal_condition_required",
    ):
        assert bridge[key] is True
    assert bridge["formal_evidence_handoff"] is False
    assert bridge["qualification_granted"] is False

    recovery = payload["recovery_branch"]
    for key in (
        "same_branch_cache_scope_supported",
        "fast_forward_to_intended_main_when_linear",
        "dispatch_from_cache_visible_ref",
        "fail_before_materialization_on_wrong_ref",
        "remove_one_time_guard_only_after_current_generation_immutable_completion",
    ):
        assert recovery[key] is True
    assert recovery["force_update_default"] is False

    resume = payload["resume_validation"]
    assert resume["cache_step_success_alone_is_insufficient"] is True
    assert resume["fully_resumed_provider_queries_executed_expected"] == 0
    assert resume["fully_resumed_documents_executed_expected"] == 0
    for key in (
        "resumed_counters_must_be_plausible",
        "current_semantic_materialization_required",
        "current_qualification_required",
        "current_generation_immutable_publish_required",
        "targeted_semantic_reproof_may_execute_only_affected_subset",
    ):
        assert resume[key] is True

    runtime = payload["runtime_estimation"]
    for key in (
        "classify_fully_resumed_units",
        "classify_targeted_semantic_repair_units",
        "classify_provider_recompute_units",
        "forecast_by_execution_class_and_parallelism",
        "do_not_apply_single_unit_average_to_mixed_recovery",
    ):
        assert runtime[key] is True

    handoff = payload["post_success_handoff_freeze"]
    for key in (
        "record_group_asset_base",
        "record_bundle_identity",
        "record_compatibility_key",
        "record_archive_sha256",
        "record_stage_receipt_sha256",
        "record_original_source_commit",
        "freeze_producer_fingerprint_inputs_until_downstream_consumed",
        "logical_retirement_may_precede_physical_cleanup",
        "physical_cleanup_deferred_if_it_changes_producer_identity",
        "cleanup_requires_separate_review_after_handoff",
        "documentation_outside_producer_identity_may_continue",
    ):
        assert handoff[key] is True


def test_cancellation_contract_requires_pre_and_post_cancel_persistence_checks() -> None:
    cancellation = _contract()["cancellation"]
    for key in (
        "inspect_completed_immutable_units",
        "inspect_active_progress_save_state",
        "prove_cancel_save_behavior",
        "verify_post_cancel_save_outcomes",
        "cancel_for_deterministic_global_blocker",
        "cancel_for_material_fixed_performance_regression_when_progress_safe",
        "do_not_cancel_only_for_slowness_when_progress_unsafe",
    ):
        assert cancellation[key] is True


def test_protocol_and_run_plan_cover_recent_long_run_failure_modes() -> None:
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
    run_plan = RUN_PLAN_PATH.read_text(encoding="utf-8")

    for phrase in (
        "Query cache and document cache are different assets",
        "Gate testing rule",
        "Cancellation decision protocol",
        "Code-change decision protocol during a live run",
        "Compatibility bridge requirements",
        "timeout is a **per-job safety ceiling**",
        "Immutable completed work-unit bundle",
        "Cache visibility is part of the restore identity",
        "provider/source query executed count = 0",
        "workflow-inline restore/resolver guard",
        "Post-success handoff freeze",
        "fully resumed units",
        "targeted semantic-repair units",
    ):
        assert phrase in protocol

    for heading in (
        "## Identity",
        "## Authorization boundary",
        "## Execution units",
        "## Persistence",
        "## Restore precedence",
        "## Preflight",
        "## Observability",
        "## Failure matrix",
        "## Cancellation criteria",
        "## Acceptance",
    ):
        assert heading in run_plan

    for phrase in (
        "Cache-producing branch/ref:",
        "Intended recovery dispatch branch/ref:",
        "intended recovery ref can read required caches",
        "wrong-ref path fails before expensive materialization",
        "zero-provider-query recovery proof:",
        "zero-document-execution recovery proof:",
        "Published asset base / bundle identity / compatibility key:",
        "Producer-identity freeze starts when:",
        "Stage success explicitly distinguished from final pipeline qualification:",
    ):
        assert phrase in run_plan


def test_fundamental_reference_workflow_obeys_long_run_ordering_contract() -> None:
    text = FUNDAMENTAL_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text

    assert "timeout-minutes: 360" in text
    assert "fail-fast: false" in text
    assert "max-parallel: 4" in text

    restore_unit = text.index("Restore immutable completed work unit")
    restore_progress = text.index("Restore durable V9 progress")
    restore_presentation_current = text.index(
        "Restore presentation-fix-compatible current-run V9 progress"
    )
    restore_presentation_prior = text.index(
        "Restore presentation-fix-compatible prior V9 progress"
    )
    restore_queries = text.index(
        "Restore cancelled-run mixed V8/V9 cache for legacy queries"
    )
    materialize = text.index("Materialize fundamental/earnings shard")
    save_progress = text.index("Save durable V9 work-unit progress")
    diagnostics = text.index("Preserve work-unit diagnostics before qualification gate")
    gate = text.index("Qualification gate")
    publish = text.index("Publish immutable completed work unit")

    assert (
        restore_unit
        < restore_progress
        < restore_presentation_current
        < restore_presentation_prior
        < restore_queries
        < materialize
    )
    assert materialize < save_progress < diagnostics < gate < publish

    save_block = text[save_progress:diagnostics]
    assert "always()" in save_block

    query_restore_block = text[restore_queries:materialize]
    assert "if: ${{ steps.work_unit_restore.outputs.reused != 'true' }}" in query_restore_block
    before_migration = query_restore_block.split(
        "Migrate validated cancelled-run V9 checkpoints into durable progress"
    )[0]
    assert "durable_progress_restore.outputs.cache-hit" not in before_migration
    assert "presentation_current_progress_restore.outputs.cache-hit" not in before_migration
    assert "presentation_prior_progress_restore.outputs.cache-hit" not in before_migration


def test_qualification_gate_has_actual_cli_entrypoint_regression_coverage() -> None:
    test_text = (ROOT / "tests" / "test_v4a_stage_qualifiable.py").read_text(
        encoding="utf-8"
    )
    assert "test_main_emits_fundamental_tolerance_payload_without_nameerror" in test_text
    assert "_MODULE.main()" in test_text


def test_fundamental_recovery_branch_scope_and_resolver_execution_are_locked() -> None:
    workflow = FUNDAMENTAL_WORKFLOW.read_text(encoding="utf-8")
    boundary_test = (ROOT / "tests" / "test_v4a_workflow_boundary.py").read_text(
        encoding="utf-8"
    )
    incident = (
        ROOT / "docs" / "v4a_fundamental_long_run_incident_review_2026-09-19.md"
    ).read_text(encoding="utf-8")

    assert "presentation progress caches are branch-scoped" in workflow
    assert "v4a/fundamental-resume-295710" in workflow
    assert "_fundamental_reuse_resolver_python" in boundary_test
    assert "test_fundamental_reuse_resolver_accepts_exact_recovery_cache_branch" in boundary_test
    assert "test_fundamental_reuse_resolver_rejects_default_branch_cache_scope" in boundary_test
    assert "35451946515" in incident
    assert "executed_symbol_queries = 0" in incident
    assert "executed_documents = 0" in incident


def test_fundamental_success_closeout_records_handoff_and_freeze_window() -> None:
    incident = (
        ROOT / "docs" / "v4a_fundamental_long_run_incident_review_2026-09-19.md"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "v4a_manual_reusable_stage_runbook.md").read_text(
        encoding="utf-8"
    )

    for text in (incident, runbook):
        assert "35451946515" in text
        assert "v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17" in text
        assert "f558db45af7a9e8b5e69dad1275f98e93e6ee49c09914fa591edd4348cfde4c4" in text
        assert "56e8c44ca678668bfc17fd7ef5da5a9c32503dcbac03eb86fa3963ecd0cf751b" in text
        assert "c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e" in text
        assert "2b27a25584e22521811ceebfdc873363d6a8fc43cf82d1b442d8ced121b91770" in text

    assert "producer-identity freeze" in incident.lower()
    assert "Producer-identity freeze before Derived" in runbook
    assert "HISTORICAL_DATA_QUALIFIED" in runbook
