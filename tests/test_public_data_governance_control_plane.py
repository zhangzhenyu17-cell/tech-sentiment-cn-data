from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "reference" / "public_data_governance_control_plane_v1.json"
QUARANTINE_PATH = ROOT / "reference" / "public_data_quarantine_ledger_v1.json"


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_control_plane_is_engineering_guard_only() -> None:
    payload = _load(CONTROL_PATH)
    assert payload["schema_version"] == "public-data-governance-control-plane-v1"
    assert payload["status"] == "ENGINEERING_GOVERNANCE_GUARD_NOT_EVIDENCE_AUTHORITY"

    authority = payload["authority"]
    for key in (
        "may_promote_evidence",
        "may_change_evidence_eligibility",
        "may_change_pit_no_lookahead_semantics",
        "may_change_model_factor_threshold_signal",
        "may_change_production",
        "may_authorize_trading",
        "public_workflow_success_grants_private_qualification",
        "semantic_review_grants_private_qualification",
        "quarantine_release_grants_private_qualification",
    ):
        assert authority[key] is False


def test_lifecycle_separates_execution_structure_semantics_and_private_intake() -> None:
    payload = _load(CONTROL_PATH)
    lifecycle = payload["lifecycle"]
    assert lifecycle["ordered_gates"] == [
        "REPOSITORY_CONTRACT",
        "DATA_CONTRACT",
        "LINEAGE_PROVENANCE",
        "STRUCTURAL_QUALITY",
        "PIT_NO_LOOKAHEAD",
        "SEMANTIC_ARTIFACT_REVIEW",
        "QUARANTINE_CHECK",
        "PUBLIC_HANDOFF_READY",
    ]
    rules = lifecycle["rules"]
    for key in (
        "workflow_success_is_only_execution_status",
        "structural_acceptance_does_not_imply_semantic_correctness",
        "semantic_review_is_outcome_blind",
        "semantic_review_precedes_private_intake",
        "active_quarantine_blocks_downstream_consumption",
        "public_handoff_ready_does_not_grant_private_qualification",
        "unknown_or_ambiguous_state_fails_closed",
    ):
        assert rules[key] is True


def test_registered_products_have_existing_contract_and_producer_paths() -> None:
    payload = _load(CONTROL_PATH)
    products = payload["registered_products"]
    assert len(products) >= 4

    ids: set[str] = set()
    for product in products:
        product_id = product["product_id"]
        assert product_id not in ids
        ids.add(product_id)
        assert (ROOT / product["contract_path"]).exists()
        assert (ROOT / product["producer_path"]).exists()
        assert product["public_workflow_success_grants_private_qualification"] is False
        assert product["may_promote_evidence"] is False


def test_parser_semantic_change_invalidates_only_affected_semantic_layers_by_default() -> None:
    payload = _load(CONTROL_PATH)
    parser_change = payload["semantic_change_policy"]["PARSER_SEMANTIC_CHANGE"]
    assert parser_change["default_invalidation"] == [
        "PARSED_DOCUMENT_FACTS",
        "DOWNSTREAM_DERIVED_DATASETS",
    ]
    assert parser_change["query_index_cache_reusable_if_query_identity_unchanged"] is True
    assert parser_change["parsed_document_cache_reusable_across_parser_semantic_versions"] is False

    protected = payload["semantic_change_policy"]["PIT_OR_EVIDENCE_SEMANTIC_CHANGE"]
    assert protected["automatic_engineering_change_allowed"] is False
    assert protected["explicit_human_authorization_required"] is True


def test_quarantine_ledger_fail_closes_known_extended_pit_v1_artifact() -> None:
    ledger = _load(QUARANTINE_PATH)
    assert ledger["schema_version"] == "public-data-quarantine-ledger-v1"
    assert ledger["status"] == "ACTIVE_ENGINEERING_QUARANTINE_GUARD_NOT_EVIDENCE_AUTHORITY"

    authority = ledger["authority"]
    for key in (
        "may_promote_evidence",
        "may_change_evidence_eligibility",
        "may_change_pit_no_lookahead_semantics",
        "may_change_production",
        "may_authorize_trading",
    ):
        assert authority[key] is False

    entries = ledger["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["quarantine_id"] == "FUND_EXT_PIT_V1_RUN_35847821341_SEMANTIC_RISK"
    assert entry["source_run_id"] == 35847821341
    assert entry["source_head_sha"] == "9f71cd2d42735911fff26294a350f8ee4635084c"
    assert entry["artifact_id"] == 10744657001
    assert entry["artifact_digest"] == (
        "sha256:4082a76bcd6567d3e27f07950842f3bc951efc80df834ccb8a4de951164d3069"
    )
    assert entry["artifact_parser_generation"] == "official-filing-extended-pit-primitives-v1"
    assert entry["state"] == "ACTIVE"
    assert entry["downstream_consumption_allowed"] is False
    assert entry["private_qualification_intake_allowed"] is False
    assert entry["may_promote_evidence"] is False
    assert entry["may_change_production"] is False
    assert entry["may_authorize_trading"] is False
    assert entry["resolved"] is False

    cache_reuse = entry["cache_reuse"]
    assert cache_reuse["query_index_cache_reusable_if_identity_unchanged"] is True
    assert cache_reuse["parsed_document_facts_reusable"] is False

    release = entry["release_condition"]
    assert release["replacement_parser_generation"] == (
        "official-filing-extended-pit-primitives-v2-column-safe"
    )
    assert release["new_run_on_new_source_sha_required"] is True
    assert release["structural_artifact_acceptance_required"] is True
    assert release["outcome_blind_semantic_artifact_review_required"] is True
    assert release["private_qualification_is_separate_after_release"] is True


def test_external_patterns_are_adapted_without_runtime_dependencies() -> None:
    payload = _load(CONTROL_PATH)
    sources = payload["design_sources"]
    assert set(sources) == {"openlineage", "odcs", "gx_core"}
    for source in sources.values():
        assert source["runtime_dependency_added"] is False
        assert source["adopted_concepts"]

    ci = payload["ci_enforcement"]
    assert ci["existing_workflow_only"] is True
    assert ci["new_workflow_added"] is False
    assert ci["audit_entrypoint"] == "scripts/audit_public_tree.py"
    assert ci["pytest_contract"] == "tests/test_public_data_governance_control_plane.py"
