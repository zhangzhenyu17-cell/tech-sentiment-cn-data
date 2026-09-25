from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.innovation_drug_company_valuation_v1 import (
    TARGET_SYMBOL,
    build_company_valuation_raw_v1,
)
from tech_sentiment.pit_price_materialization import materialize_pit_stock_prices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols-csv", type=Path, required=True)
    parser.add_argument("--filing-facts-csv", type=Path, required=True)
    parser.add_argument("--trading-calendar-csv", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    scope = pd.read_csv(args.symbols_csv, dtype=str)
    symbols = scope["symbol"].astype(str).str.zfill(6).tolist()
    if symbols != [TARGET_SYMBOL]:
        raise ValueError(f"valuation scope must be exact [{TARGET_SYMBOL}]: {symbols}")

    calendar = pd.read_csv(args.trading_calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("trading calendar missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()
    facts = pd.read_csv(args.filing_facts_csv)

    prices = materialize_pit_stock_prices(
        [TARGET_SYMBOL],
        start_date=args.start_date,
        end_date=args.end_date,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
    )
    if prices.summary.get("readiness_state") != "QUALIFIED_INPUT":
        raise RuntimeError(f"600276 price rail is not complete: {prices.summary}")

    result = build_company_valuation_raw_v1(
        filing_facts=facts,
        stock_prices=prices.prices,
        trading_dates=trading_dates,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if result.summary["usable_rows"] <= 0:
        raise RuntimeError("600276 raw valuation rail has no usable PIT rows")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    prices.prices.to_csv(args.out_dir / "pit_stock_prices.csv", index=False)
    prices.coverage.to_csv(args.out_dir / "pit_stock_price_coverage.csv", index=False)
    prices.errors.to_csv(args.out_dir / "pit_stock_price_errors.csv", index=False)
    result.rail.to_csv(args.out_dir / "trailing_valuation_rail.csv", index=False)
    result.evidence.to_csv(
        args.out_dir / "derived_pit_trailing_valuation.csv", index=False
    )
    summary = dict(result.summary)
    summary["price_materialization"] = prices.summary
    (args.out_dir / "innovation_drug_company_valuation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
