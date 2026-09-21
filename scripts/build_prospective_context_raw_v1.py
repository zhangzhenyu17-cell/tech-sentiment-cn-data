from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.prospective_context_raw_v1 import (
    materialize_public_raw_capture,
    package_capture,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture public-only same-day prospective Context raw inputs. "
            "Historical rows are warm-up known at capture time only and may not "
            "be used for retrospective evidence qualification."
        )
    )
    parser.add_argument("--operation-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("reference/prospective_context_raw_v1.json"),
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=Path("dist/prospective_context_raw"),
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(".cache/prospective_context_raw"),
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=Path("dist/prospective_context_packages"),
    )
    args = parser.parse_args()

    repo_root = Path(".").resolve()
    result = materialize_public_raw_capture(
        repo_root=repo_root,
        contract_path=args.contract.resolve(),
        operation_date=args.operation_date,
        source_commit=args.source_commit,
        output_root=args.capture_root.resolve(),
        checkpoint_dir=args.checkpoint_dir.resolve(),
    )
    manifest = package_capture(
        result.output_root,
        output_dir=args.package_dir.resolve(),
        operation_date=args.operation_date,
    )
    print(
        json.dumps(
            {
                "receipt": result.receipt,
                "package": manifest,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
