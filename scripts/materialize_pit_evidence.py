from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.pit_public_materialization import materialize_cninfo_archive


def _symbols(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual-only PIT evidence materialization from registered public sources."
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated six-digit A-share codes")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-csv", required=True, help="Real trading calendar CSV from the same Capital qualification run")
    parser.add_argument("--calendar-date-column", default="date")
    parser.add_argument("--out-dir", default="output/pit_evidence_materialization")
    args = parser.parse_args()

    symbols = _symbols(args.symbols)
    if not symbols:
        raise SystemExit("at least one symbol is required")

    calendar_frame = pd.read_csv(args.calendar_csv)
    if args.calendar_date_column not in calendar_frame.columns:
        raise SystemExit(f"calendar missing date column: {args.calendar_date_column}")
    trading_dates = pd.to_datetime(
        calendar_frame[args.calendar_date_column], errors="raise"
    ).dt.normalize()
    if trading_dates.empty:
        raise SystemExit("real trading calendar is empty")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = materialize_cninfo_archive(
        symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        trading_dates=trading_dates,
    )
    result.records.to_csv(out / "cninfo_pit_evidence.csv", index=False)
    result.coverage.to_csv(out / "cninfo_source_coverage.csv", index=False)
    result.errors.to_csv(out / "cninfo_materialization_errors.csv", index=False)
    (out / "pit_materialization_manifest.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
