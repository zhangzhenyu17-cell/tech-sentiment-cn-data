from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.innovation_drug_931152_issuer_identity_v1 import (
    membership_identity,
    membership_symbols,
    resolve_issuer_identities,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve frozen 931152 member issuer legal names from SSE/SZSE official APIs.")
    parser.add_argument("--membership-scope-csv", type=Path, required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    membership_bytes = args.membership_scope_csv.read_bytes()
    frame = pd.read_csv(args.membership_scope_csv, dtype={"symbol": str})
    symbols = membership_symbols(frame)
    result = resolve_issuer_identities(
        symbols,
        membership_scope_sha256=membership_identity(membership_bytes),
        captured_at=args.captured_at,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "issuer_identity_registry.json").write_text(
        json.dumps(result.registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "issuer_identity_raw_responses.json").write_text(
        json.dumps(result.raw_responses, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "cde_nmpa_931152_entity_mapping_v1.json").write_text(
        json.dumps(result.mapping_registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"resolved": len(result.rows), "unresolved": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
