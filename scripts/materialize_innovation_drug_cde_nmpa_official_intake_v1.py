from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.innovation_drug_cde_nmpa_official_intake_v1 import (
    materialize_official_capture_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize an outcome-blind CDE/NMPA official raw snapshot. "
            "Exact entity mapping only; unmapped applicants are retained; no score/outcome read."
        )
    )
    parser.add_argument("--raw-snapshot-csv", type=Path, required=True)
    parser.add_argument("--snapshot-manifest-json", type=Path, required=True)
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
    result = materialize_official_capture_files(
        raw_snapshot_csv=args.raw_snapshot_csv,
        manifest_json=args.snapshot_manifest_json,
        contract_json=args.contract_json,
        mapping_registry_json=args.mapping_registry_json,
        trading_calendar_csv=args.trading_calendar_csv,
        output_dir=args.output_dir,
    )
    print(json.dumps(result.summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
