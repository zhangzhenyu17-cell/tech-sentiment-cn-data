from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.innovation_drug_cde_nmpa_931152_sector_v1 import materialize_931152_sector_cde_files


def main() -> int:
    p=argparse.ArgumentParser(description="Materialize 931152 listed-issuer query-set CDE raw context with PIT membership filtering.")
    p.add_argument("--raw-snapshot-csv",type=Path,required=True)
    p.add_argument("--query-receipt-json",type=Path,required=True)
    p.add_argument("--contract-json",type=Path,required=True)
    p.add_argument("--mapping-registry-json",type=Path,required=True)
    p.add_argument("--issuer-registry-json",type=Path,required=True)
    p.add_argument("--membership-scope-csv",type=Path,required=True)
    p.add_argument("--trading-calendar-csv",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args()
    result=materialize_931152_sector_cde_files(
        raw_snapshot_csv=a.raw_snapshot_csv,
        query_receipt_json=a.query_receipt_json,
        contract_json=a.contract_json,
        mapping_registry_json=a.mapping_registry_json,
        issuer_registry_json=a.issuer_registry_json,
        membership_scope_csv=a.membership_scope_csv,
        trading_calendar_csv=a.trading_calendar_csv,
        output_dir=a.output_dir,
    )
    print(json.dumps(result.summary,ensure_ascii=False,sort_keys=True,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
