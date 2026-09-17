from __future__ import annotations

import argparse
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from tech_sentiment.capital_input_data import qualify_trailing_etf_coverage
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.resumable_capital import materialize_capital_monthly


def _etf_readiness(coverage: pd.DataFrame) -> str:
    if coverage.empty or not bool(coverage["observed"].any()):
        return "DATA_INSUFFICIENT"
    mature = coverage.iloc[60:].copy() if len(coverage) > 60 else coverage.iloc[0:0].copy()
    if mature.empty:
        return "PARTIAL_COVERAGE"
    required = (
        mature["eligible"].fillna(False)
        & mature["endpoint_20d_available"].fillna(False)
        & mature["endpoint_60d_available"].fillna(False)
    )
    if bool(required.all()) and bool(coverage.iloc[0]["observed"]):
        return "QUALIFIED_INPUT"
    return "PARTIAL_COVERAGE"


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual-only public capital-input qualification diagnostic.")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--fund-code", default="588000")
    parser.add_argument("--calendar-index-code", default="000906")
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", default=".cache/capital_pit_v4a/capital")
    parser.add_argument("--out-dir", default="output/capital_input_qualification")
    args = parser.parse_args()

    if args.end_date is None:
        args.end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calendar = fetch_index_history(
        args.calendar_index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if calendar.empty:
        raise SystemExit("trading calendar source returned no rows")
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["date"], errors="raise")).normalize().sort_values().unique()

    chunked = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=[args.fund_code],
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
        sleep_seconds=args.sleep_seconds,
    )
    etf = chunked.etf
    turnover = chunked.turnover
    coverage = qualify_trailing_etf_coverage(
        etf.data,
        trading_dates=dates,
        fund_code=args.fund_code,
        window=60,
        min_coverage=0.80,
    )
    shares = pd.to_numeric(coverage["fund_shares"], errors="coerce")
    coverage["endpoint_20d_available"] = shares.notna() & shares.shift(20).notna()
    coverage["endpoint_60d_available"] = shares.notna() & shares.shift(60).notna()

    calendar.to_csv(out / "trading_calendar.csv", index=False)
    etf.data.to_csv(out / "sse_etf_shares.csv", index=False)
    etf.errors.to_csv(out / "sse_etf_share_errors.csv", index=False)
    coverage.to_csv(out / "sse_etf_share_coverage.csv", index=False)
    turnover.sse.to_csv(out / "sse_a_share_turnover.csv", index=False)
    turnover.szse.to_csv(out / "szse_a_share_turnover.csv", index=False)
    turnover.combined.to_csv(out / "sse_szse_a_share_turnover.csv", index=False)
    turnover.errors.to_csv(out / "sse_szse_turnover_errors.csv", index=False)

    qualified = coverage[coverage["coverage"].notna()]
    etf_state = _etf_readiness(coverage)
    turnover_complete_pct = float(len(turnover.combined) / len(dates)) if len(dates) else None
    turnover_state = (
        "QUALIFIED_INPUT"
        if len(dates) and len(turnover.combined) == len(dates) and turnover.errors.empty
        else "PARTIAL_COVERAGE" if len(turnover.combined) else "DATA_INSUFFICIENT"
    )
    summary = {
        "status": "PUBLIC_MATERIALIZATION_STAGE_COMPLETED",
        "start_date": str(dates.min().date()),
        "end_date": str(dates.max().date()),
        "trading_days": int(len(dates)),
        "fund_code": str(args.fund_code).zfill(6),
        "source_commit": args.source_commit,
        "checkpoint_mode": "EXACT_IDENTITY_MONTHLY_CHUNKS",
        "checkpoint_resumed_chunks": int(chunked.resumed_chunks),
        "checkpoint_executed_chunks": int(chunked.executed_chunks),
        "etf_readiness_state": etf_state,
        "etf_observed_days": int(coverage["observed"].sum()),
        "etf_raw_coverage": float(coverage["observed"].mean()) if len(coverage) else None,
        "etf_trailing60_latest": float(qualified.iloc[-1]["coverage"]) if len(qualified) else None,
        "etf_trailing60_eligible_day_pct": float(qualified["eligible"].mean()) if len(qualified) else None,
        "etf_endpoint_20d_days": int(coverage["endpoint_20d_available"].sum()),
        "etf_endpoint_60d_days": int(coverage["endpoint_60d_available"].sum()),
        "etf_error_days": int(etf.errors["date"].nunique()) if len(etf.errors) else 0,
        "turnover_readiness_state": turnover_state,
        "sse_szse_turnover_complete_days": int(len(turnover.combined)),
        "sse_szse_turnover_complete_pct": turnover_complete_pct,
        "turnover_scope": "SSE_SZSE_A_SHARES",
        "canonical_all_a_state": "INCOMPLETE_BSE_NOT_INCLUDED",
        "production_or_model_output": False,
        "predictive_research_run": False,
    }
    (out / "qualification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
