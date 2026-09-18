from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.v4a_stage_artifact import verify_stage_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify one immutable V4-A stage artifact.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--receipt", default="receipt.json")
    parser.add_argument("--stage-kind", required=True)
    parser.add_argument("--stage-id", default="")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    receipt = root / args.receipt
    payload = verify_stage_receipt(
        root=root,
        receipt_path=receipt,
        source_commit=args.source_commit,
        stage_kind=args.stage_kind,
        stage_id=args.stage_id or None,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(
        json.dumps(
            {
                "stage_kind": payload.get("stage_kind"),
                "stage_id": payload.get("stage_id"),
                "receipt_sha256": payload.get("receipt_sha256"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
