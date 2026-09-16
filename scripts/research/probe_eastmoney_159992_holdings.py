from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from tech_sentiment.eastmoney_fund_holdings_evidence import (
    TRACKING_ETF_931152,
    fetch_and_parse_holdings_year,
)


STATUS = "SECONDARY_CANDIDATE_POOL_ONLY"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free one-shot probe for 159992 historical holdings from "
            "EastMoney/Tiantian Fund. It never edits the formal membership manifest."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if args.start_year > args.end_year:
        raise SystemExit("start-year must be <= end-year")

    out_dir = args.output_dir
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    batch_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    advertised_years: set[int] = set()

    for year in range(args.start_year, args.end_year + 1):
        try:
            batches, years, raw_text = fetch_and_parse_holdings_year(
                TRACKING_ETF_931152,
                year,
                topline=100,
                timeout=args.timeout,
            )
            advertised_years.update(years)
            (raw_dir / f"159992_{year}.txt").write_text(raw_text, encoding="utf-8")
            for batch in batches:
                row = asdict(batch)
                row["symbols"] = list(batch.symbols)
                row["symbol_count"] = len(batch.symbols)
                row["formal_manifest_qualified"] = False
                row["qualification_note"] = (
                    "Fund holdings are candidate-pool/cross-check evidence only. "
                    "A tracking ETF can contain IPO allocations, substitutions and "
                    "other non-index positions; independent dated index-membership "
                    "evidence is required before reconstruction can qualify."
                )
                batch_rows.append(row)
        except Exception as exc:  # preserve failures rather than silently skipping
            failures.append(
                {
                    "year": year,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    with (out_dir / "batches.jsonl").open("w", encoding="utf-8") as handle:
        for row in batch_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with (out_dir / "failures.jsonl").open("w", encoding="utf-8") as handle:
        for row in failures:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    candidate_sets = [row for row in batch_rows if row["full_report_candidate_set"]]
    report = {
        "status": STATUS,
        "fund_code": TRACKING_ETF_931152,
        "target_index": "931152",
        "requested_years": [args.start_year, args.end_year],
        "advertised_years": sorted(advertised_years),
        "report_batches": len(batch_rows),
        "full_report_candidate_sets": len(candidate_sets),
        "candidate_set_dates": sorted(str(row["report_date"]) for row in candidate_sets),
        "candidate_set_sizes": {
            str(row["report_date"]): int(row["symbol_count"]) for row in candidate_sets
        },
        "failures": len(failures),
        "manifest_updated": False,
        "model_results_read": False,
        "holdout_opened": False,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
