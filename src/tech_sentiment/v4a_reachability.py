from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re


REACHABILITY_SCHEMA_VERSION = "v4a-reachability-v2"
PRIVATE_HANDOFF_PATH = "reference/v4a_private_qualification_handoff_v2.json"


@dataclass(frozen=True)
class ReachabilityRequirement:
    item: str
    producer_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    contract_paths: tuple[str, ...]
    source_identities: tuple[str, ...]
    historical_reconstruction_path: str
    output_paths: tuple[str, ...]
    coverage_metadata: str
    provenance_metadata: str
    pit_audit_path: str
    readiness_evaluator: str
    bundle_inclusion_path: str
    private_consumer_contract: str
    requires_active_private_handoff: bool = False
    structural_blockers: tuple[str, ...] = ()


REQUIREMENTS: tuple[ReachabilityRequirement, ...] = (
    ReachabilityRequirement(
        item="588000_long_flow",
        producer_paths=(
            "scripts/qualify_capital_inputs.py",
            "src/tech_sentiment/capital_input_data.py",
            "src/tech_sentiment/resumable_capital.py",
            "src/tech_sentiment/immutable_checkpoint.py",
        ),
        test_paths=("tests/test_resumable_capital_financing.py",),
        contract_paths=(),
        source_identities=("SSE_ETF_SCALE_DAILY",),
        historical_reconstruction_path="per-real-trading-date SSE ETF-share snapshots from 2022-01-04, exact monthly checkpoint chunks, no fill",
        output_paths=(
            "output/capital_input_qualification/sse_etf_shares.csv",
            "output/capital_input_qualification/sse_etf_share_coverage.csv",
        ),
        coverage_metadata="qualification_summary.etf_*; frozen trailing-60 >=0.80 and 20d/60d endpoint availability",
        provenance_metadata="SSE_ETF_SCALE_DAILY source identity/url/provider plus exact checkpoint identities",
        pit_audit_path="exact real-trading-date rail; interpolation/forward-fill/backfill forbidden",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:588000_long_flow",
        bundle_inclusion_path="finalize_capital_pit_materialization -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="sse_szse_a_shares_turnover",
        producer_paths=(
            "scripts/qualify_capital_inputs.py",
            "src/tech_sentiment/capital_input_data.py",
            "src/tech_sentiment/resumable_capital.py",
            "src/tech_sentiment/immutable_checkpoint.py",
        ),
        test_paths=("tests/test_resumable_capital_financing.py",),
        contract_paths=(),
        source_identities=("SSE_DAILY_STOCK_OVERVIEW", "SZSE_MARKET_OVERVIEW_DAILY"),
        historical_reconstruction_path="bilateral exact-date SSE main-A+STAR and SZSE stock-minus-B turnover in exact monthly chunks",
        output_paths=("output/capital_input_qualification/sse_szse_a_share_turnover.csv",),
        coverage_metadata="qualification_summary.sse_szse_turnover_complete_pct",
        provenance_metadata="exchange source identities/URLs and exact source-commit checkpoint identities",
        pit_audit_path="bilateral same-day completeness; missing side remains missing; no fill",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:sse_szse_a_shares_turnover",
        bundle_inclusion_path="finalize_capital_pit_materialization -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="financing",
        producer_paths=(
            "scripts/materialize_financing_history.py",
            "src/tech_sentiment/financing_materialization.py",
            "src/tech_sentiment/resumable_financing.py",
            "src/tech_sentiment/immutable_checkpoint.py",
        ),
        test_paths=("tests/test_resumable_capital_financing.py",),
        contract_paths=(),
        source_identities=("SSE_MARGIN_SUMMARY", "SZSE_MARGIN_SUMMARY"),
        historical_reconstruction_path="SSE+SZSE exact-date financing materialized in exact monthly chunks with frozen source units",
        output_paths=(
            "output/financing_materialization/financing_raw_aligned.csv",
            "output/financing_materialization/financing_canonical_cny.csv",
        ),
        coverage_metadata="financing_manifest.bilateral_coverage",
        provenance_metadata="SSE=CNY, SZSE=CNY_100M, canonical=CNY plus source/query/checkpoint identities",
        pit_audit_path="exact-date bilateral alignment; unit mismatch quarantines fail closed",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:financing",
        bundle_inclusion_path="finalize_capital_pit_materialization -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="fundamental_pit",
        producer_paths=(
            "scripts/materialize_v4a_derived_pit.py",
            "src/tech_sentiment/official_filing_facts.py",
            "src/tech_sentiment/filing_materialization.py",
            "src/tech_sentiment/fundamental_pit_state.py",
            "src/tech_sentiment/immutable_checkpoint.py",
        ),
        test_paths=(
            "tests/test_official_filing_facts.py",
            "tests/test_fundamental_pit_state.py",
            "tests/test_pit_replay_audit.py",
        ),
        contract_paths=(
            "reference/v4a_fundamental_pit_state_contract_v1.json",
            "reference/v4a_qualification_tolerance_contract_v1.json",
        ),
        source_identities=(
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
            "DERIVED_PIT_FUNDAMENTAL_TRENDS",
        ),
        historical_reconstruction_path="exact official versioned filing attachments -> standardized facts -> as-of trends -> frozen FUNDAMENTAL_PIT_STATE_CONTRACT_V1",
        output_paths=(
            "output/pit_evidence_materialization/versioned_filing_facts.csv",
            "output/pit_evidence_materialization/derived_pit_fundamental_trends.csv",
            "output/pit_evidence_materialization/fundamental_state_evidence.csv",
            "output/pit_evidence_materialization/fundamental_state_coverage.csv",
        ),
        coverage_metadata="per-entity versioned-filing and required exact-prior-comparable coverage",
        provenance_metadata="official document URL/SHA256, document/revision identity, formula/contract version and source-filing support",
        pit_audit_path="event/evidence-available separation; append-only restatement; prefix/as-of/revision replay",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:fundamental_pit",
        bundle_inclusion_path="derived PIT outputs and frozen contract -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="earnings_pit",
        producer_paths=(
            "scripts/materialize_v4a_derived_pit.py",
            "src/tech_sentiment/earnings_materialization.py",
            "src/tech_sentiment/issuer_earnings_pit.py",
            "src/tech_sentiment/official_filing_facts.py",
        ),
        test_paths=(
            "tests/test_issuer_earnings_pit.py",
            "tests/test_pit_replay_audit.py",
        ),
        contract_paths=("reference/v4a_qualification_tolerance_contract_v1.json",),
        source_identities=(
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
        ),
        historical_reconstruction_path="exact issuer forecast/warning/restatement documents -> explicit standardized guidance direction; UNKNOWN remains auditable DATA_INSUFFICIENT and never means NOT_DOWN",
        output_paths=(
            "output/pit_evidence_materialization/earnings_direction_evidence.csv",
            "output/pit_evidence_materialization/earnings_direction_coverage.csv",
            "output/pit_evidence_materialization/earnings_direction_unclassified.csv",
        ),
        coverage_metadata="per-entity official source-window completeness plus explicit unclassified-document counts",
        provenance_metadata="canonical issuer source identity inherited with exact document URL/SHA256 and classifier version",
        pit_audit_path="close-based availability, revision-safe as-of/prefix replay, no price/return inference",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:earnings_pit",
        bundle_inclusion_path="earnings PIT outputs -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="valuation_pit",
        producer_paths=(
            "scripts/materialize_v4a_derived_pit.py",
            "src/tech_sentiment/pit_price_materialization.py",
            "src/tech_sentiment/trailing_valuation_pit.py",
            "src/tech_sentiment/immutable_checkpoint.py",
        ),
        test_paths=(
            "tests/test_pit_price_materialization.py",
            "tests/test_trailing_valuation_pit.py",
            "tests/test_pit_replay_audit.py",
        ),
        contract_paths=("reference/v4a_qualification_tolerance_contract_v1.json",),
        source_identities=("DERIVED_PIT_TRAILING_VALUATION",),
        historical_reconstruction_path="unadjusted PIT close plus only filing EPS denominators available as-of each date; frozen TTM formula; strict 20-observation change",
        output_paths=(
            "output/pit_evidence_materialization/trailing_valuation_rail.csv",
            "output/pit_evidence_materialization/derived_pit_trailing_valuation.csv",
        ),
        coverage_metadata="eligible/usable valuation rows plus explicit row-level DATA_INSUFFICIENT counts; missing rows emit no evidence",
        provenance_metadata="price provider/date plus exact filing document/revision/SHA256 and frozen formula version",
        pit_audit_path="no future denominator, no current-snapshot backfill, as-of/prefix/revision replay",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:valuation_pit",
        bundle_inclusion_path="valuation PIT outputs -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="major_event_pit",
        producer_paths=(
            "scripts/materialize_pit_evidence.py",
            "scripts/materialize_v4a_derived_pit.py",
            "src/tech_sentiment/official_pit_archives.py",
            "src/tech_sentiment/official_policy_archive.py",
            "src/tech_sentiment/resumable_policy_archive.py",
        ),
        test_paths=(
            "tests/test_official_policy_archive.py",
            "tests/test_major_negative_review.py",
            "tests/test_pit_replay_audit.py",
        ),
        contract_paths=(),
        source_identities=(
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
        ),
        historical_reconstruction_path="issuer archives plus fixed-allowlist official CSRC policy/regulatory archive with exact page/document checkpoints",
        output_paths=(
            "output/pit_evidence_materialization/official_policy_regulatory_notice_archive.csv",
            "output/pit_evidence_materialization/official_policy_regulatory_coverage.csv",
        ),
        coverage_metadata="issuer per-entity windows plus complete fixed CSRC list-segment pagination",
        provenance_metadata="official publication/document identity, URL/SHA256 and close-based availability",
        pit_audit_path="close-based availability and immutable revision-aware replay",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:major_event_pit",
        bundle_inclusion_path="issuer/policy PIT outputs -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="major_negative_exclusion",
        producer_paths=(
            "scripts/materialize_v4a_derived_pit.py",
            "src/tech_sentiment/major_negative_review.py",
            "src/tech_sentiment/pit_replay_audit.py",
        ),
        test_paths=(
            "tests/test_major_negative_review.py",
            "tests/test_pit_replay_audit.py",
        ),
        contract_paths=(),
        source_identities=(
            "CNINFO_ANNOUNCEMENT_ARCHIVE",
            "SSE_ANNOUNCEMENT_ARCHIVE",
            "SZSE_ANNOUNCEMENT_ARCHIVE",
            "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE",
        ),
        historical_reconstruction_path="applicability-aware required-source coverage ledger plus deterministic adverse-event review; NMPA/CDE only if an already-frozen entity applicability exists",
        output_paths=(
            "output/pit_evidence_materialization/major_negative_coverage_ledger.csv",
            "output/pit_evidence_materialization/major_negative_review.csv",
        ),
        coverage_metadata="every applicable source/entity/window COMPLETE_WINDOW; review completeness separate from exclusion_clear",
        provenance_metadata="negative review preserves immutable source evidence IDs and does not equate missing captures with no event",
        pit_audit_path="coverage/review completeness plus combined PIT no-future/prefix/as-of/revision audit",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:major_negative_exclusion",
        bundle_inclusion_path="coverage/review outputs -> immutable managed-file manifest",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
    ),
    ReachabilityRequirement(
        item="clean_forward_external_evidence",
        producer_paths=(
            "scripts/finalize_capital_pit_materialization.py",
            "scripts/summarize_v4a_checkpoints.py",
            "src/tech_sentiment/v4a_data_readiness.py",
            "src/tech_sentiment/pit_replay_audit.py",
            "src/tech_sentiment/materialization_manifest.py",
        ),
        test_paths=(
            "tests/test_pit_replay_audit.py",
            "tests/test_immutable_checkpoint.py",
        ),
        contract_paths=(PRIVATE_HANDOFF_PATH,),
        source_identities=(),
        historical_reconstruction_path="all nine Capital/PIT inputs plus immutable checkpoint receipts, PIT replay/provenance and major-negative gates",
        output_paths=(
            "output/materialization_identity/readiness_matrix.json",
            "output/materialization_identity/coverage_matrix.json",
            "output/materialization_identity/provenance_matrix.json",
            "output/pit_evidence_materialization/checkpoint_receipt_summary.json",
        ),
        coverage_metadata="combined nine-item data readiness and checkpoint receipt identity coverage",
        provenance_metadata="combined immutable source/provenance matrix plus contract/checkpoint SHA256 identities",
        pit_audit_path="required-fields/no-future/duplicate/provenance/prefix/as-of/revision/append-only gates",
        readiness_evaluator="tech_sentiment.v4a_data_readiness.build_v4a_data_readiness:clean_forward_external_evidence",
        bundle_inclusion_path="immutable materialization bundle + post-upload artifact receipt",
        private_consumer_contract="V4A_PRIVATE_QUALIFICATION_HANDOFF_V2",
        requires_active_private_handoff=True,
    ),
)


def _private_handoff_blockers(root: Path) -> list[str]:
    path = root / PRIVATE_HANDOFF_PATH
    if not path.is_file():
        return [f"private_handoff_missing:{PRIVATE_HANDOFF_PATH}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ["private_handoff_invalid_json"]
    if not isinstance(payload, dict):
        return ["private_handoff_not_object"]
    blockers: list[str] = []
    if payload.get("handoff_id") != "V4A_PRIVATE_QUALIFICATION_HANDOFF_V2":
        blockers.append("private_handoff_id_mismatch")
    if payload.get("public_schema_version") != "capital-pit-materialization-v4a2":
        blockers.append("private_handoff_public_schema_mismatch")
    if payload.get("public_workflow") != "qualify-capital-inputs":
        blockers.append("private_handoff_public_workflow_mismatch")
    if payload.get("public_success_semantics") != "PUBLIC_MATERIALIZATION_COMPLETED":
        blockers.append("private_handoff_public_success_semantics_mismatch")
    if payload.get("public_grants_historical_qualification") is not False:
        blockers.append("public_private_qualification_boundary_invalid")
    if payload.get("private_verifier_repository") != "zhangzhenyu17-cell/tech-sentiment-cn":
        blockers.append("private_verifier_repository_mismatch")
    if (
        payload.get("private_verifier_module")
        != "tech_sentiment.v4a_artifact_intake.verify_v4a_artifact"
    ):
        blockers.append("private_verifier_module_mismatch")
    if payload.get("private_verifier_contract_id") != "v4a_artifact_intake_contract_v2":
        blockers.append("private_verifier_contract_id_mismatch")
    if payload.get("activation_state") != "ACTIVE":
        blockers.append("private_v4a2_verifier_not_activated")
    merge_sha = str(payload.get("private_verifier_merge_sha") or "").strip().lower()
    if not merge_sha:
        blockers.append("private_v4a2_verifier_merge_sha_missing")
    elif re.fullmatch(r"[0-9a-f]{40}", merge_sha) is None:
        blockers.append("private_v4a2_verifier_merge_sha_invalid")
    if payload.get("formal_public_long_run_allowed_before_activation") is not False:
        blockers.append("preactivation_long_run_boundary_invalid")
    if payload.get("formal_public_long_run_allowed_after_activation") is not True:
        blockers.append("postactivation_long_run_boundary_invalid")
    if payload.get("forward_outcome_read_required") is not False:
        blockers.append("forward_outcome_boundary_invalid")
    if payload.get("parameter_search_required") is not False:
        blockers.append("parameter_search_boundary_invalid")
    if payload.get("production_or_trading_change_required") is not False:
        blockers.append("production_trading_boundary_invalid")
    return blockers


def assess_reachability(repo_root: str | Path) -> dict[str, object]:
    root = Path(repo_root)
    rows: list[dict[str, object]] = []
    for requirement in REQUIREMENTS:
        missing_producers = [path for path in requirement.producer_paths if not (root / path).is_file()]
        missing_tests = [path for path in requirement.test_paths if not (root / path).is_file()]
        missing_contracts = [path for path in requirement.contract_paths if not (root / path).is_file()]
        missing_fields: list[str] = []
        for name in (
            requirement.historical_reconstruction_path,
            requirement.coverage_metadata,
            requirement.provenance_metadata,
            requirement.pit_audit_path,
            requirement.readiness_evaluator,
            requirement.bundle_inclusion_path,
            requirement.private_consumer_contract,
        ):
            if not str(name).strip():
                missing_fields.append("required_contract_field")
        blockers = list(requirement.structural_blockers)
        blockers.extend(f"producer_path_missing:{path}" for path in missing_producers)
        blockers.extend(f"test_path_missing:{path}" for path in missing_tests)
        blockers.extend(f"contract_path_missing:{path}" for path in missing_contracts)
        blockers.extend(missing_fields)
        if requirement.requires_active_private_handoff:
            blockers.extend(_private_handoff_blockers(root))
        rows.append(
            {
                **asdict(requirement),
                "structurally_reachable": not blockers,
                "blockers": blockers,
            }
        )
    unreachable = [row["item"] for row in rows if not row["structurally_reachable"]]
    return {
        "schema_version": REACHABILITY_SCHEMA_VERSION,
        "state": "REACHABLE" if not unreachable else "FAIL_CLOSED",
        "formal_long_run_allowed": not unreachable,
        "unreachable_items": unreachable,
        "requirements": rows,
    }


def assert_formal_run_reachable(repo_root: str | Path) -> dict[str, object]:
    report = assess_reachability(repo_root)
    if not report["formal_long_run_allowed"]:
        blocked = ", ".join(report["unreachable_items"])
        raise RuntimeError(f"V4-A formal long run is structurally unreachable: {blocked}")
    return report


__all__ = [
    "REACHABILITY_SCHEMA_VERSION",
    "PRIVATE_HANDOFF_PATH",
    "ReachabilityRequirement",
    "REQUIREMENTS",
    "assess_reachability",
    "assert_formal_run_reachable",
]
