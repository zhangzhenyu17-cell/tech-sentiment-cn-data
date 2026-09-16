from __future__ import annotations

import argparse
from pathlib import Path

from .index_price import fetch_index_history


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download an official A-share index price rail for research joins."
    )
    parser.add_argument("--index-code", required=True, help="e.g. 000688 or 399673")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default="index_prices.csv")
    return parser


def main() -> None:
    args = _parser().parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    prices = fetch_index_history(
        args.index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if prices.empty:
        raise SystemExit(f"No index history returned for {args.index_code}.")
    prices.to_csv(out, index=False, date_format="%Y-%m-%d")
    print(
        f"index_code={prices['index_code'].iloc[0]} rows={len(prices)} "
        f"first_date={prices['date'].min().date()} last_date={prices['date'].max().date()}"
    )
    print(f"output={out.resolve()}")


if __name__ == "__main__":
    main()

