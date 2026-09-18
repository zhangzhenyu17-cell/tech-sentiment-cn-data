from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.canonical_materialization import canonicalize_metadata
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.resumable_financing import materialize_financing_monthly


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual-only official SSE/SZSE financing history materialization."
    )
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-index-code", default="000906")
    parser.add_argument("--calendar-csv", default="")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", default=".cache/capital_pit_v4a/financing")
    parser.add_argument("--out-dir", default="output/financing_materialization")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.calendar_csv:
        calendar = pd.read_csv(args.calendar_csv)
        if "date" not in calendar.columns:
            raise SystemExit("calendar CSV missing date column")
    else:
        calendar = fetch_index_history(
            args.calendar_index_code,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    if calendar.empty:
        raise SystemExit("trading calendar source returned no rows")
    dates = pd.DatetimeIndex(
        pd.to_datetime(calendar["date"], errors="raise")
    ).normalize().sort_values().unique()

    resumed = materialize_financing_monthly(
        trading_dates=dates,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
    )
    result = resumed.result
    summary_with_runtime = dict(result.summary)
    summary_with_runtime["source_commit"] = args.source_commit
    summary_with_runtime["checkpoint_mode"] = "EXACT_IDENTITY_MONTHLY_CHUNKS"
    summary = canonicalize_metadata(summary_with_runtime)
    assert isinstance(summary, dict)

    calendar.to_csv(out / "trading_calendar.csv", index=False)
    result.raw.to_csv(out / "financing_raw_aligned.csv", index=False)
    result.canonical.to_csv(out / "financing_canonical_cny.csv", index=False)
    result.errors.to_csv(out / "financing_errors.csv", index=False)
    (out / "financing_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "canonical_summary": summary,
                "runtime_diagnostics": {
                    "resumed_chunks": int(resumed.resumed_chunks),
                    "executed_chunks": int(resumed.executed_chunks),
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
