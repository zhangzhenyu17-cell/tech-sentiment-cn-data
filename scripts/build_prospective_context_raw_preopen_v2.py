from __future__ import annotations

import argparse
from datetime import datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from tech_sentiment.prospective_context_raw_preopen_v2 import package_preopen_capture
from tech_sentiment.prospective_context_raw_v1 import materialize_public_raw_capture
from tech_sentiment.prospective_formal_v3_sources import (
    FORMAL_ARTIFACT_DEADLINE,
    SHANGHAI,
    load_formal_v3_capital_sources,
)



def _require_capture_completed_before_freeze(
    market_session_date: str,
    decision_date: str,
    *,
    completed_at: datetime | None = None,
) -> None:
    """Legacy V2 timing guard retained for historical contract regression only."""
    now = completed_at or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    else:
        now = now.astimezone(SHANGHAI)
    session_date = datetime.strptime(market_session_date, "%Y-%m-%d").date()
    decision = datetime.strptime(decision_date, "%Y-%m-%d").date()
    operational_start = datetime.combine(session_date, time(23, 45), tzinfo=SHANGHAI)
    freeze = datetime.combine(decision, time(5, 30), tzinfo=SHANGHAI)
    if freeze < operational_start:
        raise RuntimeError(
            "PREOPEN_CAPTURE_INVALID_OPERATIONAL_WINDOW: "
            f"session={market_session_date} decision={decision_date}"
        )
    if now < operational_start:
        raise RuntimeError(
            "PREOPEN_CAPTURE_COMPLETED_BEFORE_2345_OPERATIONAL_WINDOW: "
            f"session={market_session_date} decision={decision_date} "
            f"completed_at={now.isoformat()}"
        )
    if now > freeze:
        raise RuntimeError(
            "PREOPEN_CAPTURE_COMPLETED_AFTER_0530_FREEZE: "
            f"session={market_session_date} decision={decision_date} "
            f"completed_at={now.isoformat()}"
        )


def _require_capture_completed_before_formal_v3_deadline(
    decision_date: str,
    *,
    completed_at: datetime | None = None,
) -> None:
    now = completed_at or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    else:
        now = now.astimezone(SHANGHAI)
    decision = datetime.strptime(decision_date, "%Y-%m-%d").date()
    deadline = datetime.combine(decision, FORMAL_ARTIFACT_DEADLINE, tzinfo=SHANGHAI)
    if now > deadline:
        raise RuntimeError(
            "FORMAL_V3_PUBLIC_RAW_COMPLETED_AFTER_0915_DEADLINE: "
            f"decision={decision_date} completed_at={now.isoformat()}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble the formal public Context bundle for market-session T using "
            "verified Prospective Capture V3 source observations plus the frozen "
            "public Context rails."
        )
    )
    parser.add_argument("--market-session-date", required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--v3-package-root",
        type=Path,
        required=True,
        help="Root containing one verified immutable V3 package directory per source key.",
    )
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
    formal_sources = load_formal_v3_capital_sources(
        package_root=args.v3_package_root.resolve(),
        market_session_date=args.market_session_date,
        decision_date=args.decision_date,
    )
    result = materialize_public_raw_capture(
        repo_root=root,
        contract_path=args.data_contract.resolve(),
        operation_date=args.market_session_date,
        decision_date=args.decision_date,
        source_commit=args.source_commit,
        output_root=args.capture_root.resolve(),
        checkpoint_dir=args.checkpoint_dir.resolve(),
        timing_mode="FORMAL_V3",
        formal_v3_capital_sources=formal_sources,
    )
    _require_capture_completed_before_formal_v3_deadline(
        args.decision_date,
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
