from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.prospective_context_raw_preopen_v2 import (
    materialize_preopen_public_raw_capture,
    package_preopen_capture,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture public-only market-session T inputs for a next-trading-day "
            "pre-open decision, with all data frozen no later than 05:30 "
            "Asia/Shanghai."
        )
    )
    parser.add_argument("--market-session-date", required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--data-contract",
        type=Path,
        default=Path("reference/prospective_context_raw_v1.json"),
    )
    parser.add_argument(
        "--timing-contract",
        type=Path,
        default=Path("reference/prospective_context_raw_preopen_v2.json"),
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=Path("dist/prospective_context_raw_preopen_v2"),
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(".cache/prospective_context_raw"),
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=Path("dist/prospective_context_preopen_packages"),
    )
    args = parser.parse_args()

    root = Path(".").resolve()
    result = materialize_preopen_public_raw_capture(
        repo_root=root,
        data_contract_path=args.data_contract.resolve(),
        timing_contract_path=args.timing_contract.resolve(),
        market_session_date=args.market_session_date,
        decision_date=args.decision_date,
        source_commit=args.source_commit,
        output_root=args.capture_root.resolve(),
        checkpoint_dir=args.checkpoint_dir.resolve(),
    )
    manifest = package_preopen_capture(
        result.output_root,
        output_dir=args.package_dir.resolve(),
        market_session_date=args.market_session_date,
        decision_date=args.decision_date,
    )
    print(
        json.dumps(
            {"receipt": result.receipt, "package": manifest},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
