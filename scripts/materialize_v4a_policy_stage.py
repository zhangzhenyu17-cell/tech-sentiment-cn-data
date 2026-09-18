from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.resumable_policy_archive import materialize_csrc_policy_archive_resumable
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records


SCHEMA_VERSION = "v4a-policy-stage-v1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize the V4-A official policy/regulatory stage.")
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()
    result = materialize_csrc_policy_archive_resumable(
        start_date=args.start_date,
        end_date=args.end_date,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
    )
    records = result.records
    if len(records):
        records = validate_materialized_pit_records(records)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records.to_csv(out / "official_policy_regulatory_notice_archive.csv", index=False)
    result.coverage.to_csv(out / "official_policy_regulatory_coverage.csv", index=False)
    result.errors.to_csv(out / "official_policy_regulatory_errors.csv", index=False)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "start_date": str(pd.Timestamp(args.start_date).date()),
        "end_date": str(pd.Timestamp(args.end_date).date()),
        "policy_materialization": result.summary,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
