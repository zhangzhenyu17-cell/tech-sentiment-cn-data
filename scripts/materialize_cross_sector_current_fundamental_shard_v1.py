from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.filing_materialization import materialize_versioned_filing_facts
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records

SCHEMA_VERSION = "cross-sector-current-fundamental-shard-v1"
TARGET_START_DATE = "2026-01-01"
END_DATE = "2026-09-30"
WARMUP_YEARS = 1


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
    return [value for position, value in enumerate(sorted(values)) if position % count == index]


def _canonicalize(records: pd.DataFrame) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame() if records is None else records.copy()
    return validate_materialized_pit_records(records.copy())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize one outcome-blind current Fundamental shard for frozen cross-sector domains."
    )
    parser.add_argument("--symbols-csv", type=Path, required=True)
    parser.add_argument("--calendar-csv", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    all_symbols = _read_scope(args.symbols_csv)
    symbols = _shard(all_symbols, index=args.shard_index, count=args.shard_count)
    if not symbols:
        raise SystemExit("current Fundamental shard has no symbols")

    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()

    filings = materialize_versioned_filing_facts(
        symbols,
        target_start_date=TARGET_START_DATE,
        end_date=END_DATE,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir / "filings",
        warmup_years=WARMUP_YEARS,
    )
    fundamental = materialize_fundamental_state_evidence(
        filings.facts,
        target_start_date=TARGET_START_DATE,
        target_end_date=END_DATE,
    )

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    filings.facts.to_csv(out / "versioned_filing_facts.csv", index=False)
    _canonicalize(fundamental.evidence).to_csv(
        out / "fundamental_state_evidence.csv", index=False
    )
    fundamental.coverage.to_csv(out / "fundamental_state_coverage.csv", index=False)
    filings.coverage.to_csv(out / "filing_coverage.csv", index=False)
    filings.errors.to_csv(out / "filing_errors.csv", index=False)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "target_start_date": TARGET_START_DATE,
        "end_date": END_DATE,
        "warmup_years": WARMUP_YEARS,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "filing_materialization": filings.summary,
        "fundamental_state_contract": fundamental.summary,
        "outcome_read": False,
        "historical_outcome_read": False,
        "prospective_outcome_read": False,
        "parameter_search_run": False,
        "predictive_research_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
