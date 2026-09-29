from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tech_sentiment.prospective_source_observation_v3 import (
    capture_capital_source_observations_v3,
    package_complete_source_observation_v3,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture source-level public observations for Prospective Capture V3 shadow mode."
    )
    parser.add_argument("--market-session-date", required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--sources",
        default=",".join(("SSE_588000", "SZSE_159915", "SSE_TURNOVER", "SZSE_TURNOVER")),
        help="Comma-separated source keys to attempt.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("dist/prospective_source_observations_v3"),
    )
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path("dist/prospective_source_observation_packages_v3"),
    )
    args = parser.parse_args()

    observed_at = datetime.now(SHANGHAI)
    source_keys = tuple(value.strip() for value in args.sources.split(",") if value.strip())
    observations = capture_capital_source_observations_v3(
        market_session_date=args.market_session_date,
        decision_date=args.decision_date,
        source_commit=args.source_commit,
        output_root=args.output_root,
        observed_at=observed_at,
        transport_origin=os.getenv("PROSPECTIVE_TRANSPORT_ORIGIN", "GITHUB_HOSTED"),
        runner_name=os.getenv("RUNNER_NAME", ""),
        source_keys=source_keys,
    )
    manifests = []
    for observation in observations.values():
        if observation.state == "COMPLETE":
            manifests.append(
                package_complete_source_observation_v3(
                    observation,
                    output_dir=args.package_root,
                )
            )
    print(json.dumps({
        "market_session_date": args.market_session_date,
        "decision_date": args.decision_date,
        "complete_packages": manifests,
        "formal_evidence_handoff": False,
        "forward_outcomes_read": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
