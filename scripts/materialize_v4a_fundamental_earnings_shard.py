from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.earnings_materialization import materialize_cninfo_earnings_directions
from tech_sentiment.filing_materialization import materialize_versioned_filing_facts
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records


SCHEMA_VERSION = "v4a-fundamental-earnings-shard-v1"


def _read_scope(path: str | Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("scope CSV missing symbol")
    return sorted({str(value).zfill(6) for value in frame["symbol"].dropna().astype(str)})


def _shard(values: list[str], *, index: int, count: int) -> list[str]:
    if count < 1 or index < 0 or index >= count:
        raise ValueError("invalid shard index/count")
    return [value for position, value in enumerate(sorted(values)) if position % count == index]


def _canonicalize_provenance(records: pd.DataFrame) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame() if records is None else records.copy()
    out = records.copy()
    values: list[str] = []
    for _, row in out.iterrows():
        payload = json.loads(str(row["provenance"]))
        if not isinstance(payload, dict):
            raise ValueError("PIT provenance must be a JSON object")
        payload.setdefault("source_identity", str(row["source_identity"]))
        payload.setdefault("provider", str(row["provider"]))
        values.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    out["provenance"] = values
    return validate_materialized_pit_records(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize one V4-A fundamental/earnings symbol shard.")
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument(
        "--filing-checkpoint-source-commit",
        default=None,
        help="Optional frozen legacy source commit used only to address immutable filing checkpoints.",
    )
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    all_symbols = _read_scope(args.symbols_csv)
    symbols = _shard(all_symbols, index=args.shard_index, count=args.shard_count)
    if not symbols:
        raise SystemExit("fundamental/earnings shard has no symbols")
    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()
    start = pd.Timestamp(args.start_date).normalize()
    end = pd.Timestamp(args.end_date).normalize()

    checkpoint = Path(args.checkpoint_dir)
    filings = materialize_versioned_filing_facts(
        symbols,
        target_start_date=start,
        end_date=end,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=checkpoint / "filings",
        warmup_years=2,
        checkpoint_source_commit=args.filing_checkpoint_source_commit,
    )
    fundamental = materialize_fundamental_state_evidence(
        filings.facts,
        target_start_date=start,
        target_end_date=end,
    )
    earnings = materialize_cninfo_earnings_directions(
        symbols,
        query_start_date=start - pd.DateOffset(years=2),
        end_date=end,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=checkpoint / "filings",
    )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    filings.facts.to_csv(out / "versioned_filing_facts.csv", index=False)
    _canonicalize_provenance(filings.trends).to_csv(
        out / "derived_pit_fundamental_trends.csv", index=False
    )
    _canonicalize_provenance(fundamental.evidence).to_csv(
        out / "fundamental_state_evidence.csv", index=False
    )
    fundamental.coverage.to_csv(out / "fundamental_state_coverage.csv", index=False)
    filings.coverage.to_csv(out / "filing_coverage.csv", index=False)
    filings.errors.to_csv(out / "filing_errors.csv", index=False)
    earnings.directions.to_csv(out / "earnings_direction.csv", index=False)
    _canonicalize_provenance(earnings.evidence).to_csv(
        out / "earnings_direction_evidence.csv", index=False
    )
    earnings.coverage.to_csv(out / "earnings_direction_coverage.csv", index=False)
    earnings.errors.to_csv(out / "earnings_direction_errors.csv", index=False)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "shard_index": int(args.shard_index),
        "shard_count": int(args.shard_count),
        "symbols": symbols,
        "symbol_count": len(symbols),
        "filing_materialization": filings.summary,
        "filing_checkpoint_source_commit": (
            args.filing_checkpoint_source_commit or args.source_commit
        ),
        "fundamental_state_contract": fundamental.summary,
        "earnings_materialization": earnings.summary,
        "future_prices_or_returns_used": False,
        "parameter_search_run": False,
        "predictive_research_run": False,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
