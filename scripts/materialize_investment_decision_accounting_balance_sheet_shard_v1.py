from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.extended_filing_materialization import materialize_extended_filing_facts
from tech_sentiment.official_filing_accounting_balance_sheet_v1 import (
    ACCOUNTING_BALANCE_SHEET_FACT_LABELS,
    ACCOUNTING_BALANCE_SHEET_PARSER_VERSION,
    build_accounting_balance_sheet_fact_rows,
)

SCHEMA_VERSION = "investment-decision-accounting-balance-sheet-shard-v1"
TARGET_START_DATE = "2026-01-01"
END_DATE = "2026-09-30"
WARMUP_YEARS = 2


def _read_scope(path: Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("scope CSV missing symbol")
    values = sorted({str(value).zfill(6) for value in frame["symbol"].dropna()})
    if not values:
        raise ValueError("scope CSV is empty")
    return values


def _shard(values: list[str], *, index: int, count: int) -> list[str]:
    if count < 1 or index < 0 or index >= count:
        raise ValueError("invalid shard index/count")
    return [value for position, value in enumerate(values) if position % count == index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols-csv", type=Path, required=True)
    parser.add_argument("--calendar-csv", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-financial-documents-per-symbol", type=int)
    args = parser.parse_args()

    symbols = _shard(
        _read_scope(args.symbols_csv),
        index=args.shard_index,
        count=args.shard_count,
    )
    if not symbols:
        raise SystemExit("accounting balance-sheet shard has no symbols")

    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()

    result = materialize_extended_filing_facts(
        symbols,
        target_start_date=TARGET_START_DATE,
        end_date=END_DATE,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
        warmup_years=WARMUP_YEARS,
        max_financial_documents_per_symbol=args.max_financial_documents_per_symbol,
        parser_version=ACCOUNTING_BALANCE_SHEET_PARSER_VERSION,
        fact_row_builder=build_accounting_balance_sheet_fact_rows,
    )

    expected = set(ACCOUNTING_BALANCE_SHEET_FACT_LABELS)
    facts = result.facts.copy()
    if len(facts):
        unexpected = set(facts["fact_type"].dropna().astype(str)) - expected
        if unexpected:
            raise ValueError(f"unexpected accounting balance-sheet facts: {sorted(unexpected)}")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    facts.to_csv(out / "accounting_balance_sheet_facts.csv", index=False)
    result.coverage.to_csv(out / "coverage.csv", index=False)
    result.errors.to_csv(out / "errors.csv", index=False)

    hard_failures = (
        int((result.errors["severity"] == "HARD_FAILURE").sum())
        if len(result.errors) and "severity" in result.errors.columns
        else 0
    )
    fact_counts = (
        facts.groupby("fact_type")["entity_id"].nunique().astype(int).to_dict()
        if len(facts)
        else {}
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "parser_version": ACCOUNTING_BALANCE_SHEET_PARSER_VERSION,
        "target_start_date": TARGET_START_DATE,
        "end_date": END_DATE,
        "warmup_years": WARMUP_YEARS,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "fact_types": sorted(expected),
        "fact_entity_counts": {
            key: int(value) for key, value in sorted(fact_counts.items())
        },
        "hard_failure_rows": hard_failures,
        "materialization_summary": result.summary,
        "historical_forward_outcome_read": False,
        "prospective_forward_outcome_read": False,
        "predictive_research_run": False,
        "private_operating_financing_classification_included": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
