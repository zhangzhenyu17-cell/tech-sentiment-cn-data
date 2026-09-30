from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.cross_sector_relative_mispricing_public_v0 import (
    build_cross_sector_public_input,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize public-only CSI price and rolling-PE rails for Cross-Sector Relative Mispricing V0."
    )
    parser.add_argument("--as-of-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--start-date", default="2024-01-01", help="YYYY-MM-DD")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--output-csv",
        default="data/reference/cross_sector_relative_mispricing_v0_public_input_latest.csv",
    )
    parser.add_argument(
        "--output-manifest",
        default="reference/cross_sector_relative_mispricing_v0_public_input_latest.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = build_cross_sector_public_input(
        start_date=args.start_date,
        as_of_date=args.as_of_date,
        source_commit=args.source_commit,
        output_csv=Path(args.output_csv),
        output_manifest=Path(args.output_manifest),
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
