from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import tarfile

from tech_sentiment.materialization_manifest import (
    build_materialization_manifest,
    file_sha256,
    write_manifest,
)
from tech_sentiment.v4a_data_readiness import build_v4a_data_readiness


SCHEMA_VERSION = "capital-pit-materialization-v4a2"
PUBLIC_COMPLETED_STATUS = "PUBLIC_MATERIALIZATION_COMPLETED"

MANAGED_RELATIVE_PATHS = (
    "capital_input_qualification/trading_calendar.csv",
    "capital_input_qualification/sse_etf_shares.csv",
    "capital_input_qualification/sse_etf_share_errors.csv",
    "capital_input_qualification/sse_etf_share_coverage.csv",
    "capital_input_qualification/sse_a_share_turnover.csv",
    "capital_input_qualification/szse_a_share_turnover.csv",
    "capital_input_qualification/sse_szse_a_share_turnover.csv",
    "capital_input_qualification/sse_szse_turnover_errors.csv",
    "capital_input_qualification/qualification_summary.json",
    "financing_materialization/trading_calendar.csv",
    "financing_materialization/financing_raw_aligned.csv",
    "financing_materialization/financing_canonical_cny.csv",
    "financing_materialization/financing_errors.csv",
    "financing_materialization/financing_manifest.json",
    "pit_symbol_scope/capital_pit_symbols.csv",
    "pit_symbol_scope/capital_pit_symbol_scope.json",
    "pit_evidence_materialization/pit_evidence.csv",
    "pit_evidence_materialization/pit_source_coverage.csv",
    "pit_evidence_materialization/pit_materialization_errors.csv",
    "pit_evidence_materialization/pit_materialization_manifest.json",
    "pit_evidence_materialization/cninfo_announcement_archive_evidence.csv",
    "pit_evidence_materialization/cninfo_announcement_archive_coverage.csv",
    "pit_evidence_materialization/cninfo_announcement_archive_errors.csv",
    "pit_evidence_materialization/sse_announcement_archive_evidence.csv",
    "pit_evidence_materialization/sse_announcement_archive_coverage.csv",
    "pit_evidence_materialization/sse_announcement_archive_errors.csv",
    "pit_evidence_materialization/szse_announcement_archive_evidence.csv",
    "pit_evidence_materialization/szse_announcement_archive_coverage.csv",
    "pit_evidence_materialization/szse_announcement_archive_errors.csv",
    "pit_evidence_materialization/versioned_filing_facts.csv",
    "pit_evidence_materialization/derived_pit_fundamental_trends.csv",
    "pit_evidence_materialization/fundamental_state_evidence.csv",
    "pit_evidence_materialization/fundamental_state_coverage.csv",
    "pit_evidence_materialization/fundamental_pit_state_contract_v1.json",
    "pit_evidence_materialization/earnings_direction.csv",
    "pit_evidence_materialization/earnings_direction_evidence.csv",
    "pit_evidence_materialization/earnings_direction_coverage.csv",
    "pit_evidence_materialization/pit_stock_prices.csv",
    "pit_evidence_materialization/pit_stock_price_coverage.csv",
    "pit_evidence_materialization/pit_stock_price_errors.csv",
    "pit_evidence_materialization/trailing_valuation_rail.csv",
    "pit_evidence_materialization/derived_pit_trailing_valuation.csv",
    "pit_evidence_materialization/official_policy_regulatory_notice_archive.csv",
    "pit_evidence_materialization/official_policy_regulatory_coverage.csv",
    "pit_evidence_materialization/official_policy_regulatory_errors.csv",
    "pit_evidence_materialization/major_negative_coverage_ledger.csv",
    "pit_evidence_materialization/major_negative_review.csv",
    "pit_evidence_materialization/pit_evidence_extended.csv",
    "pit_evidence_materialization/derived_pit_materialization_manifest.json",
    "pit_evidence_materialization/checkpoint_receipt_summary.json",
)


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _managed_files(root: Path) -> list[Path]:
    missing = [relative for relative in MANAGED_RELATIVE_PATHS if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"required V4-A materialization files missing: {missing}")
    return [root / relative for relative in MANAGED_RELATIVE_PATHS]


def _deterministic_tar(archive_path: Path, *, root: Path, files: list[Path]) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
                    relative = path.relative_to(root).as_posix()
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize immutable public V4-A materialization identities; private repo grants qualification."
    )
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--out-dir", default="output/materialization_identity")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()

    root = Path(args.output_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    capital_dir = root / "capital_input_qualification"
    financing_dir = root / "financing_materialization"
    pit_dir = root / "pit_evidence_materialization"
    scope_dir = root / "pit_symbol_scope"

    capital_summary = _read_json(capital_dir / "qualification_summary.json")
    financing_summary = _read_json(financing_dir / "financing_manifest.json")
    pit_summary = _read_json(pit_dir / "derived_pit_materialization_manifest.json")
    issuer_summary = _read_json(pit_dir / "pit_materialization_manifest.json")
    scope_summary = _read_json(scope_dir / "capital_pit_symbol_scope.json")
    contract = _read_json(pit_dir / "fundamental_pit_state_contract_v1.json")
    checkpoint_summary = _read_json(pit_dir / "checkpoint_receipt_summary.json")

    if contract.get("contract_id") != "FUNDAMENTAL_PIT_STATE_CONTRACT_V1":
        raise ValueError("unexpected fundamental PIT contract identity")
    if contract.get("parameter_search") is not False:
        raise ValueError("fundamental PIT contract must prove parameter_search=false")
    if checkpoint_summary.get("source_commit") != str(args.source_commit):
        raise ValueError("checkpoint receipt summary source commit mismatch")
    if checkpoint_summary.get("all_completion_states_complete") is not True:
        raise ValueError("checkpoint receipt summary contains incomplete chunks")

    readiness = build_v4a_data_readiness(
        capital_summary=capital_summary,
        financing_summary=financing_summary,
        pit_summary=pit_summary,
    )
    all_v4a_items = (
        "588000_long_flow",
        "sse_szse_a_shares_turnover",
        "financing",
        "fundamental_pit",
        "earnings_pit",
        "valuation_pit",
        "major_event_pit",
        "major_negative_exclusion",
        "clean_forward_external_evidence",
    )
    data_ready_for_private_verification = all(
        readiness.get(item) == "QUALIFIED_INPUT" for item in all_v4a_items
    )

    coverage_matrix: dict[str, object] = {
        "588000_long_flow": {
            "observed_days": capital_summary.get("etf_observed_days"),
            "raw_coverage": capital_summary.get("etf_raw_coverage"),
            "trailing60_latest": capital_summary.get("etf_trailing60_latest"),
            "trailing60_eligible_day_pct": capital_summary.get("etf_trailing60_eligible_day_pct"),
            "endpoint_20d_days": capital_summary.get("etf_endpoint_20d_days"),
            "endpoint_60d_days": capital_summary.get("etf_endpoint_60d_days"),
            "error_days": capital_summary.get("etf_error_days"),
        },
        "sse_szse_a_shares_turnover": {
            "complete_days": capital_summary.get("sse_szse_turnover_complete_days"),
            "complete_pct": capital_summary.get("sse_szse_turnover_complete_pct"),
            "scope": capital_summary.get("turnover_scope"),
        },
        "financing": {
            "bilateral_coverage": financing_summary.get("bilateral_coverage"),
            "qualification_state": financing_summary.get("qualification_state"),
        },
        "pit_symbol_scope": scope_summary,
        "issuer_pit_sources": issuer_summary.get("source_summaries", {}),
        "fundamental": pit_summary.get("fundamental_coverage", {}),
        "valuation": pit_summary.get("valuation_coverage", {}),
        "policy": pit_summary.get("policy_materialization", {}),
        "major_negative": pit_summary.get("major_negative_summary", {}),
        "checkpoint_receipts": {
            "receipt_count": checkpoint_summary.get("receipt_count"),
            "all_completion_states_complete": checkpoint_summary.get("all_completion_states_complete"),
            "all_source_commits_match": checkpoint_summary.get("all_source_commits_match"),
        },
    }
    provenance_matrix: dict[str, object] = {
        "capital_market": {
            "etf_source": "SSE_ETF_SCALE_DAILY",
            "turnover_sources": ["SSE_DAILY_STOCK_OVERVIEW", "SZSE_MARKET_OVERVIEW_DAILY"],
            "interpolation": False,
            "forward_fill": False,
            "backfill": False,
            "checkpoint_mode": capital_summary.get("checkpoint_mode"),
        },
        "financing": {
            "sources": ["SSE_MARGIN_SUMMARY", "SZSE_MARGIN_SUMMARY"],
            "sse_raw_unit": "CNY",
            "szse_raw_unit": "CNY_100M",
            "canonical_unit": "CNY",
            "research_input_only": True,
            "checkpoint_mode": financing_summary.get("checkpoint_mode"),
        },
        "pit": {
            "source_states": pit_summary.get("source_states", {}),
            "audit": pit_summary.get("pit_audit", {}),
            "fundamental_state_contract": {
                "contract_id": contract.get("contract_id"),
                "contract_version": contract.get("contract_version"),
                "file_sha256": file_sha256(pit_dir / "fundamental_pit_state_contract_v1.json"),
                "threshold_policy": contract.get("threshold_policy"),
            },
            "major_negative_event_exclusion_complete": bool(
                pit_summary.get("major_negative_event_exclusion_complete")
            ),
            "hindsight_backfill": False,
            "future_prices_or_returns_used": False,
            "parameter_search_run": False,
        },
        "checkpoint_receipts": {
            "schema_version": checkpoint_summary.get("schema_version"),
            "summary_sha256": file_sha256(pit_dir / "checkpoint_receipt_summary.json"),
            "source_commit": checkpoint_summary.get("source_commit"),
        },
    }

    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_run_id": str(args.workflow_run_id or "").strip() or None,
        "source_commit": str(args.source_commit or "").strip() or None,
        "target_start": capital_summary.get("start_date"),
        "target_end": capital_summary.get("end_date"),
        "overall_status": PUBLIC_COMPLETED_STATUS,
        "readiness_matrix": readiness,
        "coverage_matrix": coverage_matrix,
        "provenance_matrix": provenance_matrix,
        "data_ready_for_private_verification": data_ready_for_private_verification,
        "private_qualification_required": True,
        "public_repo_grants_historical_qualification": False,
        "v4c_context_research_gate_met": False,
        "research_run": False,
        "evidence_qualification_promotion_run": False,
        "production_run": False,
        "workflow_dispatch_only_required": True,
    }
    report_path = out / "qualification_report.json"
    _write_json(report_path, report)
    report_sha256 = file_sha256(report_path)

    managed_files = _managed_files(root)
    source_identities = [
        "SSE_ETF_SCALE_DAILY",
        "SSE_DAILY_STOCK_OVERVIEW",
        "SZSE_MARKET_OVERVIEW_DAILY",
        "SSE_MARGIN_SUMMARY",
        "SZSE_MARGIN_SUMMARY",
    ]
    raw_source_states = pit_summary.get("source_states")
    if isinstance(raw_source_states, dict):
        source_identities.extend(str(value) for value in raw_source_states.keys())

    query_identities: dict[str, object] = {
        "pit_symbol_scope_identity": scope_summary.get("scope_identity"),
        "financing_source_query_identity": financing_summary.get("source_query_identity"),
        "issuer_pit_source_summaries": issuer_summary.get("source_summaries"),
        "fundamental_contract_id": contract.get("contract_id"),
        "fundamental_contract_sha256": file_sha256(
            pit_dir / "fundamental_pit_state_contract_v1.json"
        ),
        "checkpoint_receipt_summary_sha256": file_sha256(
            pit_dir / "checkpoint_receipt_summary.json"
        ),
        "pit_audit": pit_summary.get("pit_audit"),
    }

    manifest = build_materialization_manifest(
        schema_version=SCHEMA_VERSION,
        target_start=str(capital_summary["start_date"]),
        target_end=str(capital_summary["end_date"]),
        root=root,
        files=managed_files + [report_path],
        readiness_matrix=readiness,
        source_identities=source_identities,
        query_identities=query_identities,
        workflow_run_id=args.workflow_run_id,
        source_commit=args.source_commit,
        coverage_matrix=coverage_matrix,
        provenance_matrix=provenance_matrix,
    )
    manifest_path = out / "capital_pit_materialization_manifest.json"
    write_manifest(manifest_path, manifest)
    readiness_path = out / "readiness_matrix.json"
    coverage_path = out / "coverage_matrix.json"
    provenance_path = out / "provenance_matrix.json"
    _write_json(readiness_path, readiness)
    _write_json(coverage_path, coverage_matrix)
    _write_json(provenance_path, provenance_matrix)

    bundle_files = managed_files + [
        report_path,
        manifest_path,
        readiness_path,
        coverage_path,
        provenance_path,
    ]
    archive_path = out / "capital_pit_qualification_bundle.tar.gz"
    _deterministic_tar(archive_path, root=root, files=bundle_files)
    bundle_sha256 = file_sha256(archive_path)
    (out / "capital_pit_qualification_bundle.sha256").write_text(
        f"{bundle_sha256}  {archive_path.name}\n", encoding="utf-8"
    )
    identity = {
        "schema_version": SCHEMA_VERSION,
        "workflow_run_id": str(args.workflow_run_id or "").strip() or None,
        "source_commit": str(args.source_commit or "").strip() or None,
        "manifest_sha256": file_sha256(manifest_path),
        "manifest_content_identity": manifest.get("manifest_sha256"),
        "report_sha256": report_sha256,
        "bundle_tar_sha256": bundle_sha256,
        "managed_file_count": len(manifest.get("files", [])),
        "artifact_id": None,
        "artifact_digest": None,
        "artifact_identity_note": (
            "GitHub artifact id/digest are assigned only after upload; the post-upload receipt records them "
            "without creating a circular self-hash."
        ),
    }
    _write_json(out / "bundle_identity_preupload.json", identity)
    print(json.dumps({**report, "bundle_identity": identity}, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
