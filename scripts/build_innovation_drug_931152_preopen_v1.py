from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from tech_sentiment.innovation_drug_931152_preopen_v1 import build_public_preopen_capture
from tech_sentiment.prospective_preopen_timing_v2 import SHANGHAI


def main() -> int:
    parser = argparse.ArgumentParser(description="Build public-only 931152 Innovation Drug V1 pre-open inputs.")
    parser.add_argument("--market-session-date", required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--anchor",
        type=Path,
        default=Path("data/reference/innovation_drug_931152_live_anchor_2026-09-11.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist/innovation_drug_931152_preopen_v1"),
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=Path("dist/innovation_drug_931152_preopen_packages"),
    )
    args = parser.parse_args()
    result = build_public_preopen_capture(
        market_session_date=args.market_session_date,
        decision_date=args.decision_date,
        anchor_path=args.anchor,
        output_dir=args.output_dir,
        package_dir=args.package_dir,
        source_commit=args.source_commit,
        captured_at=datetime.now(SHANGHAI),
    )
    print(result.release_tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
