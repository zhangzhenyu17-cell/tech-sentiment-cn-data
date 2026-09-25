from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

SCOPE_VERSION = "innovation-drug-company-alpha-fundamental-scope-v1"
TARGET_SYMBOL = "600276"
DESIGN_PATH = "data/reference/sector_931152_design_universe.csv"
LIVE_ANCHOR_PATH = "data/reference/innovation_drug_931152_live_anchor_2026-09-11.csv"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_scope(root: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    design_path = root / DESIGN_PATH
    anchor_path = root / LIVE_ANCHOR_PATH
    design = pd.read_csv(design_path, dtype=str)
    anchor = pd.read_csv(anchor_path, dtype=str)
    design_symbols = set(design["symbol"].dropna().astype(str).str.zfill(6))
    anchor_symbols = set(anchor["symbol"].dropna().astype(str).str.zfill(6))
    if TARGET_SYMBOL not in design_symbols:
        raise ValueError("600276 missing from frozen 931152 design membership")
    if TARGET_SYMBOL not in anchor_symbols:
        raise ValueError("600276 missing from current 931152 live anchor")
    scope = pd.DataFrame([{
        "symbol": TARGET_SYMBOL,
        "market": "SH",
        "domain_id": "INNOVATION_DRUG",
        "portfolio_role": "COMPANY_ALPHA",
        "source_index": "931152",
        "scope_mode": "EXACT_SINGLE_COMPANY_OUTCOME_BLIND_PIT",
    }])
    manifest = {
        "scope_version": SCOPE_VERSION,
        "domain_id": "INNOVATION_DRUG",
        "source_index": "931152",
        "symbols": 1,
        "target_symbol": TARGET_SYMBOL,
        "design_membership_verified": True,
        "live_anchor_membership_verified": True,
        "input_file_sha256": {
            DESIGN_PATH: _sha256(design_path),
            LIVE_ANCHOR_PATH: _sha256(anchor_path),
        },
        "outcome_read": False,
        "predictive_research_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    return scope, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    scope, manifest = build_scope(args.repo_root.resolve())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    scope.to_csv(args.out_dir / "innovation_drug_company_fundamental_scope.csv", index=False)
    (args.out_dir / "innovation_drug_company_fundamental_scope.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
