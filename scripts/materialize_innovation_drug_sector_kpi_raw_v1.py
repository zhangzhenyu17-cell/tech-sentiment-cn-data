from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.innovation_drug_sector_kpi_raw_v1 import (
    build_sector_kpi_raw_result,
    normalize_cde_snapshot,
    normalize_cninfo_sector_events,
)
from tech_sentiment.pit_public_materialization import materialize_cninfo_archive


def _trade_dates(path: Path) -> pd.DatetimeIndex:
    frame = pd.read_csv(path)
    for candidate in ("date", "trade_date", "calendar_date"):
        if candidate in frame.columns:
            column = candidate
            break
    else:
        if len(frame.columns) != 1:
            raise ValueError("trading calendar must expose date/trade_date/calendar_date")
        column = frame.columns[0]
    dates = pd.to_datetime(frame[column], errors="raise")
    if dates.empty:
        raise ValueError("real trading calendar cannot be empty")
    return pd.DatetimeIndex(dates).normalize().sort_values().unique()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize outcome-blind Innovation Drug Clinical/Regulatory/BD raw event context. "
            "No sector score, direction, outcome or predictive threshold is computed."
        )
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--trading-calendar-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--cde-snapshot-csv",
        type=Path,
        help=(
            "Optional manually captured official CDE/NMPA snapshot using the frozen schema. "
            "Absence remains fail-closed and does not block CNINFO issuer-event materialization."
        ),
    )
    args = parser.parse_args()

    symbol = str(args.symbol).zfill(6)
    trading_dates = _trade_dates(args.trading_calendar_csv)
    cninfo = materialize_cninfo_archive(
        [symbol],
        start_date=args.start_date,
        end_date=args.end_date,
        trading_dates=trading_dates,
    )
    cninfo_events = normalize_cninfo_sector_events(cninfo.records)

    cde_events = None
    cde_state = "DATA_INSUFFICIENT_NOT_MATERIALIZED"
    if args.cde_snapshot_csv is not None:
        snapshot = pd.read_csv(args.cde_snapshot_csv, dtype=str)
        cde_events = normalize_cde_snapshot(snapshot, trading_dates=trading_dates)
        cde_state = (
            "RAW_OFFICIAL_SNAPSHOT_MATERIALIZED"
            if not cde_events.empty
            else "OFFICIAL_SNAPSHOT_EMPTY"
        )

    source_coverage = {
        "CNINFO_ANNOUNCEMENT_ARCHIVE": {
            "state": "COMPLETE_WINDOW"
            if (
                len(cninfo.coverage)
                and set(cninfo.coverage["query_status"].astype(str)) == {"COMPLETE_WINDOW"}
                and cninfo.errors.empty
            )
            else "PARTIAL_OR_FAILED",
            "coverage_rows": cninfo.coverage.to_dict("records"),
            "error_rows": cninfo.errors.to_dict("records"),
            "source_records": int(len(cninfo.records)),
        },
        "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE": {
            "state": cde_state,
            "live_scraper_implemented": False,
            "official_snapshot_adapter_ready": True,
        },
    }
    result = build_sector_kpi_raw_result(
        cninfo_events=cninfo_events,
        cde_events=cde_events,
        source_coverage=source_coverage,
    )

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    cninfo.records.to_csv(out / "cninfo_pit_records.csv", index=False)
    cninfo.coverage.to_csv(out / "cninfo_coverage.csv", index=False)
    cninfo.errors.to_csv(out / "cninfo_errors.csv", index=False)
    result.events.to_csv(out / "innovation_drug_sector_kpi_raw_events.csv", index=False)
    (out / "innovation_drug_sector_kpi_raw_summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(result.summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
