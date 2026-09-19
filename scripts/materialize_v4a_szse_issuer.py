from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.materialize_pit_evidence as base
from tech_sentiment.szse_code_migration import (
    CONTRACT_PATH,
    fetch_szse_announcements_with_code_migration,
    load_migration_contract,
)


def _arg_value(flag: str) -> str:
    try:
        index = sys.argv.index(flag)
    except ValueError as exc:
        raise SystemExit(f"required argument missing: {flag}") from exc
    if index + 1 >= len(sys.argv):
        raise SystemExit(f"required argument value missing: {flag}")
    return sys.argv[index + 1]


def main() -> None:
    source = _arg_value("--source")
    if source != base.SZSE_SOURCE_ID:
        raise SystemExit("SZSE migration producer only accepts SZSE_ANNOUNCEMENT_ARCHIVE")

    contract = load_migration_contract()
    base.fetch_szse_announcements = fetch_szse_announcements_with_code_migration
    base.main()

    out_dir = Path(_arg_value("--out-dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONTRACT_PATH, out_dir / CONTRACT_PATH.name)
    print(
        json.dumps(
            {
                "diagnostic": "SZSE_SECURITY_CODE_MIGRATION_CONTRACT_APPLIED",
                "schema_version": contract["schema_version"],
                "migrations": contract["migrations"],
                "canonical_evidence_source_unchanged": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
