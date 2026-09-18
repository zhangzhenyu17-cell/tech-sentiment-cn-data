from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.v4a_stage_artifact import build_stage_receipt, write_stage_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Write an immutable V4-A stage artifact receipt.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--stage-kind", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"stage root missing: {root}")
    out = Path(args.out)
    files = [path for path in root.rglob("*") if path.is_file() and path.resolve() != out.resolve()]
    payload = build_stage_receipt(
        root=root,
        files=files,
        stage_kind=args.stage_kind,
        stage_id=args.stage_id,
        source_commit=args.source_commit,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    write_stage_receipt(out, payload)
    print(json.dumps({"stage_id": args.stage_id, "files": len(payload["files"])}, sort_keys=True))


if __name__ == "__main__":
    main()
