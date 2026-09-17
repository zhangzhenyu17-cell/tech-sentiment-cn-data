from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.capital_etf_archive_audit import audit_public_etf_share_archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local-only fail-closed audit for a candidate public SSE ETF-share archive."
    )
    parser.add_argument("--archive-csv", required=True)
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--calendar-date-column", default="date")
    parser.add_argument("--fund-code", default="588000")
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--min-coverage", type=float, default=0.80)
    parser.add_argument("--out-dir", default="output/capital_etf_archive_audit")
    args = parser.parse_args()

    archive = pd.read_csv(args.archive_csv, dtype={"fund_code": "string"})
    calendar_frame = pd.read_csv(args.calendar_csv)
    if args.calendar_date_column not in calendar_frame.columns:
        raise SystemExit(
            f"calendar missing date column: {args.calendar_date_column}"
        )

    result = audit_public_etf_share_archive(
        archive,
        trading_dates=calendar_frame[args.calendar_date_column],
        fund_code=args.fund_code,
        window=args.window,
        min_coverage=args.min_coverage,
    )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result.normalized.to_csv(out / "candidate_etf_shares_normalized.csv", index=False)
    result.coverage.to_csv(out / "candidate_etf_share_coverage.csv", index=False)
    (out / "candidate_etf_archive_audit.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
