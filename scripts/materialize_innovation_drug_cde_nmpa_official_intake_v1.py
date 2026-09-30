from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.innovation_drug_cde_nmpa_official_intake_v1 import (
    build_official_capture_manifest,
    materialize_official_capture_files,
)
from tech_sentiment.innovation_drug_cde_nmpa_snapshot_v1 import load_cde_snapshot_contract


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize an outcome-blind CDE/NMPA official raw snapshot. "
            "Exact entity mapping only; unmapped applicants are retained; no score/outcome read."
        )
    )
    parser.add_argument("--raw-snapshot-csv", type=Path, required=True)
    parser.add_argument("--snapshot-manifest-json", type=Path)
    parser.add_argument(
        "--captured-at",
        help="Timezone-aware official browser capture timestamp; required when manifest is generated.",
    )
    parser.add_argument(
        "--capture-status", choices=("COMPLETE", "PARTIAL"), default="PARTIAL"
    )
    parser.add_argument(
        "--capture-query-company",
        help=(
            "Official company-query string when completeness applies only to exhaustive "
            "pagination within that query scope, not to the full source category."
        ),
    )
    parser.add_argument(
        "--contract-json",
        type=Path,
        default=Path("reference/innovation_drug_cde_nmpa_snapshot_v1_contract.json"),
    )
    parser.add_argument(
        "--mapping-registry-json",
        type=Path,
        default=Path("reference/innovation_drug_cde_nmpa_entity_mapping_v1.json"),
    )
    parser.add_argument("--trading-calendar-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.snapshot_manifest_json
    if manifest_path is None:
        if not args.captured_at:
            parser.error("--captured-at is required when --snapshot-manifest-json is omitted")
        contract = load_cde_snapshot_contract(args.contract_json)
        import hashlib

        mapping_sha = hashlib.sha256(args.mapping_registry_json.read_bytes()).hexdigest()
        manifest = build_official_capture_manifest(
            contract=contract,
            mapping_registry_sha256=mapping_sha,
            captured_at=args.captured_at,
            capture_status=args.capture_status,
            capture_query_company=args.capture_query_company,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = args.output_dir / "cde_nmpa_snapshot_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    result = materialize_official_capture_files(
        raw_snapshot_csv=args.raw_snapshot_csv,
        manifest_json=manifest_path,
        contract_json=args.contract_json,
        mapping_registry_json=args.mapping_registry_json,
        trading_calendar_csv=args.trading_calendar_csv,
        output_dir=args.output_dir,
    )
    print(json.dumps(result.summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
