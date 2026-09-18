from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.pit_price_materialization import materialize_pit_stock_prices


SCHEMA_VERSION = "v4a-price-shard-v1"


def _symbols(path: str | Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("scope CSV missing symbol")
    return sorted({str(value).zfill(6) for value in frame["symbol"].dropna().astype(str)})


def _shard(values: list[str], index: int, count: int) -> list[str]:
    if count < 1 or index < 0 or index >= count:
        raise ValueError("invalid shard index/count")
    return [value for position, value in enumerate(values) if position % count == index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize one V4-A PIT price symbol shard.")
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    symbols = _shard(_symbols(args.symbols_csv), args.shard_index, args.shard_count)
    if not symbols:
        raise SystemExit("price shard has no symbols")
    result = materialize_pit_stock_prices(
        symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
    )
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result.prices.to_csv(out / "pit_stock_prices.csv", index=False)
    result.coverage.to_csv(out / "pit_stock_price_coverage.csv", index=False)
    result.errors.to_csv(out / "pit_stock_price_errors.csv", index=False)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "start_date": str(pd.Timestamp(args.start_date).date()),
        "end_date": str(pd.Timestamp(args.end_date).date()),
        "shard_index": int(args.shard_index),
        "shard_count": int(args.shard_count),
        "symbols": symbols,
        "symbol_count": len(symbols),
        "price_materialization": result.summary,
    }
    (out / "stage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
