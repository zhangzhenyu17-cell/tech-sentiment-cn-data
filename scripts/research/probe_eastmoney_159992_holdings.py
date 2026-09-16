from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path

from tech_sentiment.eastmoney_etf_membership import (
    TRACKING_ETF,
    fetch_holdings_archive,
    parse_holding_periods,
)


STATUS = "CANDIDATE_SECONDARY_MEMBERSHIP_EVIDENCE_ONLY"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free probe of Eastmoney/Tiantian Fund historical holdings for ETF 159992. "
            "It only collects candidate dated anchors and never edits the membership manifest."
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

    period_rows: list[dict[str, object]] = []
    holding_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for year in range(args.start_year, args.end_year + 1):
        try:
            archive = fetch_holdings_archive(year=year, timeout=args.timeout)
            raw_path = raw_dir / f"{TRACKING_ETF}_{year}.txt"
            raw_path.write_text(archive.raw_text, encoding="utf-8")
            periods = parse_holding_periods(archive.content_html)
            for period in periods:
                row = {
                    "requested_year": year,
                    "as_of": period.as_of,
                    "label": period.label,
                    "fund_report_count": period.fund_report_count,
                    "cross_reference_count": period.cross_reference_count,
                    "expandable": period.expandable,
                    "coverage_status": period.coverage_status,
                    "source_url": archive.source_url,
                    "raw_sha256": archive.raw_sha256,
                    "server_current_year": archive.current_year,
                    "server_available_years": "|".join(str(item) for item in archive.available_years),
                }
                period_rows.append(row)
                for symbol in period.fund_report_symbols:
                    holding_rows.append(
                        {
                            "requested_year": year,
                            "as_of": period.as_of,
                            "symbol": symbol,
                            "disclosure_source": "fund_report",
                            "coverage_status": period.coverage_status,
                            "source_url": archive.source_url,
                            "raw_sha256": archive.raw_sha256,
                        }
                    )
                for symbol in period.cross_reference_symbols:
                    holding_rows.append(
                        {
                            "requested_year": year,
                            "as_of": period.as_of,
                            "symbol": symbol,
                            "disclosure_source": "issuer_top10_float_holders",
                            "coverage_status": period.coverage_status,
                            "source_url": archive.source_url,
                            "raw_sha256": archive.raw_sha256,
                        }
                    )
        except Exception as exc:  # preserve failures instead of silently skipping years
            failures.append(
                {
                    "requested_year": year,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    period_fields = [
        "requested_year",
        "as_of",
        "label",
        "fund_report_count",
        "cross_reference_count",
        "expandable",
        "coverage_status",
        "source_url",
        "raw_sha256",
        "server_current_year",
        "server_available_years",
    ]
    with (out_dir / "period_audit.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=period_fields)
        writer.writeheader()
        writer.writerows(period_rows)

    holding_fields = [
        "requested_year",
        "as_of",
        "symbol",
        "disclosure_source",
        "coverage_status",
        "source_url",
        "raw_sha256",
    ]
    with (out_dir / "holdings.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=holding_fields)
        writer.writeheader()
        writer.writerows(holding_rows)

    with (out_dir / "failures.jsonl").open("w", encoding="utf-8") as handle:
        for row in failures:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    full_anchors = sorted(
        {
            str(row["as_of"])
            for row in period_rows
            if row["coverage_status"] == "candidate_full_portfolio"
        }
    )
    report = {
        "status": STATUS,
        "fund_code": TRACKING_ETF,
        "years_requested": [args.start_year, args.end_year],
        "periods_parsed": len(period_rows),
        "holding_rows": len(holding_rows),
        "candidate_full_anchor_dates": full_anchors,
        "candidate_full_anchor_count": len(full_anchors),
        "failures": len(failures),
        "manifest_updated": False,
        "qualification_note": (
            "A June/December holding period is only a candidate full anchor when the expanded "
            "fund-reported rows exceed the quarterly disclosure cap. Starred issuer cross-reference "
            "rows are excluded from the fund-report set. Secondary anchors still require independent "
            "reconciliation against index membership/change evidence before qualification."
        ),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    if not full_anchors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
