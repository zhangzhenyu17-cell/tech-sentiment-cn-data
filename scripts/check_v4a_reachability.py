from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.v4a_reachability import assess_reachability


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed structural preflight for the V4-A historical materialization run."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", default=None)
    parser.add_argument("--require-reachable", action="store_true")
    args = parser.parse_args()

    report = assess_reachability(Path(args.repo_root))
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    if args.require_reachable and report["state"] != "REACHABLE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
