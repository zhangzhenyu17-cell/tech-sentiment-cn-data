from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.innovation_drug_sector_kpi_raw_v1 import (
    SECTOR_QUERY_KEYWORDS,
    build_931152_sector_raw_summary,
    filter_events_to_pit_membership,
    materialize_cninfo_market_keyword_archive,
    normalize_cninfo_sector_events,
    validate_931152_membership_scope,
)


def _trading_dates(path: Path) -> pd.DatetimeIndex:
    frame = pd.read_csv(path)
    for column in ("trade_date", "date", "calendar_date"):
        if column in frame.columns:
            values = frame[column]
            break
    else:
        if len(frame.columns) != 1:
            raise ValueError("trading calendar must have one recognized date column")
        values = frame.iloc[:, 0]
    dates = pd.DatetimeIndex(pd.to_datetime(values, errors="raise")).normalize().sort_values().unique()
    if not len(dates):
        raise ValueError("trading calendar cannot be empty")
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize PIT-member-filtered Innovation Drug 931152 raw Clinical/Regulatory/BD "
            "event coverage from frozen market-wide CNINFO keyword pulls. No event direction, "
            "weight, score, outcome or evidence promotion is computed."
        )
    )
    parser.add_argument("--membership-scope-csv", type=Path, required=True)
    parser.add_argument("--trading-calendar-csv", type=Path, required=True)
    parser.add_argument("--start-date", default="2019-04-22")
    parser.add_argument("--end-date", default="2026-09-11")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    membership = validate_931152_membership_scope(
        pd.read_csv(args.membership_scope_csv, dtype={"symbol": str})
    )
    if str(membership["effective_start"].min().date()) != "2019-04-22":
        raise ValueError("frozen 931152 KPI membership start drift")
    if str(membership["effective_end"].max().date()) != "2026-09-11":
        raise ValueError("frozen 931152 KPI membership end drift")
    trading_dates = _trading_dates(args.trading_calendar_csv)
    if not (trading_dates > pd.Timestamp(args.end_date)).any():
        raise ValueError("trading calendar must include a successor session after sector cutoff")

    keyword_result = materialize_cninfo_market_keyword_archive(
        keywords=SECTOR_QUERY_KEYWORDS,
        start_date=args.start_date,
        end_date=args.end_date,
        trading_dates=trading_dates,
        candidate_symbols=membership["symbol"].astype(str),
    )
    if not keyword_result.query_errors.empty:
        raise ValueError(
            "market keyword materialization has failed queries: "
            + keyword_result.query_errors.to_json(orient="records", force_ascii=False)
        )

    all_title_events = normalize_cninfo_sector_events(keyword_result.pit_records)
    pit_events = filter_events_to_pit_membership(all_title_events, membership)
    summary = build_931152_sector_raw_summary(
        events=pit_events,
        membership=membership,
        query_coverage=keyword_result.query_coverage,
        query_errors=keyword_result.query_errors,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    keyword_result.pit_records.to_csv(out / "market_keyword_pit_records.csv", index=False)
    keyword_result.query_coverage.to_csv(out / "market_keyword_query_coverage.csv", index=False)
    keyword_result.query_errors.to_csv(out / "market_keyword_query_errors.csv", index=False)
    all_title_events.to_csv(out / "title_taxonomy_events_before_membership.csv", index=False)
    pit_events.to_csv(out / "sector_931152_pit_raw_events.csv", index=False)
    membership.to_csv(out / "sector_931152_kpi_membership_scope_verified.csv", index=False)
    (out / "sector_931152_kpi_raw_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
