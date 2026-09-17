from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REACHABILITY_SCHEMA_VERSION = "v4a-reachability-v1"


@dataclass(frozen=True)
class ReachabilityRequirement:
    item: str
    producer_paths: tuple[str, ...]
    source_identities: tuple[str, ...]
    historical_reconstruction_path: str
    output_paths: tuple[str, ...]
    coverage_metadata: str
    provenance_metadata: str
    pit_audit_path: str
    readiness_evaluator: str
    bundle_inclusion_path: str
    private_consumer_contract: str
    structural_blockers: tuple[str, ...] = ()


# This is a structural gate, not a data-quality result.  A requirement may only
# have its blocker removed once the referenced producer/output/audit path really
# exists and has a test proving that it can produce the required semantics.
REQUIREMENTS: tuple[ReachabilityRequirement, ...] = (
    ReachabilityRequirement(
        item="588000_long_flow",
        producer_paths=("scripts/qualify_capital_inputs.py", "src/tech_sentiment/capital_input_data.py"),
        source_identities=("SSE_ETF_SCALE_DAILY",),
        historical_reconstruction_path="per-real-trading-date SSE ETF share snapshots from 2022-01-04; no fill",
        output_paths=("output/capital_input_qualification/sse_etf_shares.csv", "output/capital_input_qualification/sse_etf_share_coverage.csv"),
        coverage_metadata="qualification_summary.etf_* and trailing-60 >= 0.80 with 20d/60d endpoints",
        provenance_metadata="SSE_ETF_SCALE_DAILY source identity/url/provider interface",
        pit_audit_path="exact real-trading-date rail; interpolation/forward-fill/backfill forbidden",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:588000_long_flow",
        bundle_inclusion_path="finalize_capital_pit_materialization -> materialization manifest",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
    ),
    ReachabilityRequirement(
        item="sse_szse_a_shares_turnover",
        producer_paths=("scripts/qualify_capital_inputs.py", "src/tech_sentiment/capital_input_data.py"),
        source_identities=("SSE_DAILY_STOCK_OVERVIEW", "SZSE_MARKET_OVERVIEW_DAILY"),
        historical_reconstruction_path="bilateral exact-date SSE main-A+STAR and SZSE stock-minus-B turnover",
        output_paths=("output/capital_input_qualification/sse_szse_a_share_turnover.csv",),
        coverage_metadata="qualification_summary.sse_szse_turnover_complete_pct",
        provenance_metadata="exchange source identities, URLs and frozen units",
        pit_audit_path="bilateral same-day completeness; missing side remains missing; no fill",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:sse_szse_a_shares_turnover",
        bundle_inclusion_path="finalize_capital_pit_materialization -> materialization manifest",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
    ),
    ReachabilityRequirement(
        item="financing",
        producer_paths=("scripts/materialize_financing_history.py", "src/tech_sentiment/financing_materialization.py"),
        source_identities=("SSE_MARGIN_SUMMARY", "SZSE_MARGIN_SUMMARY"),
        historical_reconstruction_path="SSE range history plus exact-date SZSE snapshots; frozen source units",
        output_paths=("output/financing_materialization/financing_raw_aligned.csv", "output/financing_materialization/financing_canonical_cny.csv"),
        coverage_metadata="financing_manifest.bilateral_coverage",
        provenance_metadata="SSE=CNY, SZSE=CNY_100M, canonical=CNY source identities and URLs",
        pit_audit_path="exact-date bilateral alignment; unit mismatch quarantines fail closed",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:financing",
        bundle_inclusion_path="finalize_capital_pit_materialization -> materialization manifest",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
    ),
    ReachabilityRequirement(
        item="fundamental_pit",
        producer_paths=("scripts/materialize_pit_evidence.py", "src/tech_sentiment/official_pit_archives.py"),
        source_identities=("CNINFO_ANNOUNCEMENT_ARCHIVE", "SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE", "DERIVED_PIT_FUNDAMENTAL_TRENDS"),
        historical_reconstruction_path="versioned official filings -> as-of numerical filing facts -> derived trends",
        output_paths=("output/pit_evidence_materialization/derived_pit_fundamental_trends.csv",),
        coverage_metadata="per-entity/versioned-filing coverage plus derived-field coverage",
        provenance_metadata="document/revision identity and source filing provenance for every derived value",
        pit_audit_path="event_date/evidence_available_date, append-only revision-safe as-of and prefix replay",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:fundamental_pit",
        bundle_inclusion_path="pit evidence output -> allowlisted immutable materialization bundle",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("DERIVED_PIT_FUNDAMENTAL_TRENDS producer is not implemented",),
    ),
    ReachabilityRequirement(
        item="earnings_pit",
        producer_paths=("scripts/materialize_pit_evidence.py", "src/tech_sentiment/official_pit_archives.py"),
        source_identities=("CNINFO_ANNOUNCEMENT_ARCHIVE", "SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE"),
        historical_reconstruction_path="versioned reports/forecasts/warnings/guidance/restatements -> PIT earnings state",
        output_paths=("output/pit_evidence_materialization/pit_evidence.csv",),
        coverage_metadata="per-entity source-window coverage and earnings-state payload coverage",
        provenance_metadata="document/revision identity plus source filing provenance",
        pit_audit_path="close-based availability, revision-safe as-of and prefix replay",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:earnings_pit",
        bundle_inclusion_path="pit evidence output -> allowlisted immutable materialization bundle",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("issuer archive currently classifies titles but does not produce earnings-direction/state payload",),
    ),
    ReachabilityRequirement(
        item="valuation_pit",
        producer_paths=("scripts/materialize_pit_evidence.py",),
        source_identities=("DERIVED_PIT_TRAILING_VALUATION",),
        historical_reconstruction_path="PIT market price plus latest filing denominator actually available as-of date",
        output_paths=("output/pit_evidence_materialization/derived_pit_trailing_valuation.csv",),
        coverage_metadata="per-entity/market-date valuation coverage with denominator identity",
        provenance_metadata="price identity plus exact filing/revision identity and frozen formula version",
        pit_audit_path="no future filing denominator; as-of/prefix/revision replay",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:valuation_pit",
        bundle_inclusion_path="pit evidence output -> allowlisted immutable materialization bundle",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("DERIVED_PIT_TRAILING_VALUATION producer is not implemented",),
    ),
    ReachabilityRequirement(
        item="major_event_pit",
        producer_paths=("scripts/materialize_pit_evidence.py", "src/tech_sentiment/official_pit_archives.py"),
        source_identities=("CNINFO_ANNOUNCEMENT_ARCHIVE", "SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE", "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"),
        historical_reconstruction_path="issuer archives plus official policy/regulatory published notices",
        output_paths=("output/pit_evidence_materialization/official_policy_regulatory_notice_archive.csv",),
        coverage_metadata="source/entity/applicability coverage ledger",
        provenance_metadata="publication/document identity, event/publication/effective dates where applicable",
        pit_audit_path="close-based availability and immutable revision-aware replay",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:major_event_pit",
        bundle_inclusion_path="pit evidence output -> allowlisted immutable materialization bundle",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE producer is not implemented",),
    ),
    ReachabilityRequirement(
        item="major_negative_exclusion",
        producer_paths=("scripts/materialize_pit_evidence.py",),
        source_identities=("CNINFO_ANNOUNCEMENT_ARCHIVE", "SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE", "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"),
        historical_reconstruction_path="applicability-aware required-source coverage ledger plus deterministic adverse-event review",
        output_paths=("output/pit_evidence_materialization/major_negative_coverage_ledger.csv", "output/pit_evidence_materialization/major_negative_review.csv"),
        coverage_metadata="every applicable required source/entity/window must be COMPLETE_WINDOW",
        provenance_metadata="negative-review decision references immutable source evidence identities",
        pit_audit_path="absence of captured event never implies exclusion without complete source coverage",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:major_negative_exclusion",
        bundle_inclusion_path="coverage/review outputs -> allowlisted immutable materialization bundle",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("major_negative_event_exclusion_complete is currently fixed false; coverage/review producer is not implemented",),
    ),
    ReachabilityRequirement(
        item="clean_forward_external_evidence",
        producer_paths=("scripts/finalize_capital_pit_materialization.py", "src/tech_sentiment/materialization_manifest.py"),
        source_identities=(),
        historical_reconstruction_path="all required Capital/PIT rails plus replay/provenance/major-negative gates",
        output_paths=("output/materialization_identity/readiness_matrix.json", "output/materialization_identity/provenance_matrix.json"),
        coverage_metadata="combined required-item coverage matrix",
        provenance_metadata="combined immutable source/provenance matrix",
        pit_audit_path="required-fields/no-future/duplicate/provenance/prefix/revision gates",
        readiness_evaluator="tech_sentiment.materialization_manifest.build_readiness_matrix:clean_forward_external_evidence",
        bundle_inclusion_path="immutable bundle identity + receipt",
        private_consumer_contract="tech_sentiment.v4a_artifact_intake.verify_v4a_artifact",
        structural_blockers=("depends on currently unreachable fundamental/earnings/valuation/major-event/major-negative rails",),
    ),
)


def assess_reachability(repo_root: str | Path) -> dict[str, object]:
    root = Path(repo_root)
    rows: list[dict[str, object]] = []
    for requirement in REQUIREMENTS:
        missing_paths = [
            path for path in requirement.producer_paths if not (root / path).is_file()
        ]
        missing_fields = []
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
        blockers.extend(f"producer_path_missing:{path}" for path in missing_paths)
        blockers.extend(missing_fields)
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
    "ReachabilityRequirement",
    "REQUIREMENTS",
    "assess_reachability",
    "assert_formal_run_reachable",
]
