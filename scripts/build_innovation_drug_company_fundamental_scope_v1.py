from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.innovation_drug_fundamental_scope_v1 import build_scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    scope, manifest = build_scope(args.repo_root.resolve())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    scope.to_csv(args.out_dir / "innovation_drug_company_fundamental_scope.csv", index=False)
    (args.out_dir / "innovation_drug_company_fundamental_scope.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
