from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

FACT_TYPES = (
    "INCOME_TAX_EXPENSE_CN_GAAP",
    "OPERATING_PROFIT_CN_GAAP",
    "TOTAL_PROFIT_CN_GAAP",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parts(root: Path, filename: str) -> pd.DataFrame:
    paths = sorted(root.rglob(filename))
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_csv(path, dtype={"entity_id": str}) for path in paths]
    return pd.concat(frames, ignore_index=True, sort=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--require-no-hard-failures", action="store_true")
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "INVESTMENT_DECISION_CHAIN_OPERATING_DRIVER_PRIMITIVES_V1":
        raise ValueError("unexpected driver contract")
    scope = pd.read_csv(args.scope_csv, dtype=str)
    symbols = sorted({str(value).zfill(6) for value in scope["symbol"].dropna()})
    if len(symbols) != int(contract["scope"]["symbol_count"]):
        raise ValueError("scope symbol count drift")
    if _sha(args.scope_csv) != contract["scope"]["sha256"]:
        raise ValueError("scope hash drift")

    facts = _load_parts(args.shard_root, "decision_driver_facts.csv")
    coverage = _load_parts(args.shard_root, "coverage.csv")
    errors = _load_parts(args.shard_root, "errors.csv")
    manifests = sorted(args.shard_root.rglob("stage_manifest.json"))
    if not manifests:
        raise ValueError("no shard manifests")

    if len(facts):
        facts = facts.drop_duplicates(
            ["entity_id", "document_id", "revision_id", "fact_type"], keep="last"
        ).sort_values(
            ["entity_id", "period_end", "evidence_available_date", "document_id", "fact_type"]
        )
        if not set(facts["fact_type"].astype(str)).issubset(set(FACT_TYPES)):
            raise ValueError("unexpected fact type in aggregate")
    if len(coverage):
        coverage = coverage.drop_duplicates(
            ["entity_id", "coverage_start", "coverage_end"], keep="last"
        ).sort_values("entity_id")
    if len(errors):
        errors = errors.drop_duplicates().sort_values(["entity_id", "document_id"])

    hard_failures = (
        int((errors["severity"] == "HARD_FAILURE").sum())
        if len(errors) and "severity" in errors.columns
        else 0
    )
    if args.require_no_hard_failures and hard_failures:
        raise ValueError(f"hard failures remain: {hard_failures}")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    fact_path = out / "decision_driver_facts.csv"
    coverage_path = out / "coverage.csv"
    error_path = out / "errors.csv"
    facts.to_csv(fact_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    errors.to_csv(error_path, index=False)

    entity_counts = {
        fact: int(facts.loc[facts["fact_type"].eq(fact), "entity_id"].nunique())
        if len(facts)
        else 0
        for fact in FACT_TYPES
    }
    manifest = {
        "schema_version": "investment-decision-driver-bundle-v1",
        "status": "PUBLIC_OUTCOME_BLIND_RAW_DRIVER_BUNDLE",
        "source_contract_id": contract["contract_id"],
        "decision_date": contract["decision_date"],
        "scope_symbol_count": len(symbols),
        "shard_manifest_count": len(manifests),
        "fact_types": list(FACT_TYPES),
        "fact_entity_counts": entity_counts,
        "hard_failure_rows": hard_failures,
        "files": {
            "decision_driver_facts.csv": _sha(fact_path),
            "coverage.csv": _sha(coverage_path),
            "errors.csv": _sha(error_path),
        },
        "historical_forward_outcome_read": False,
        "prospective_forward_outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "private_model_semantics_included": False,
    }
    (out / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
