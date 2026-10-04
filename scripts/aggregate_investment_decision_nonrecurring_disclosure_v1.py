from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.official_filing_nonrecurring_disclosure_v1 import (
    NONRECURRING_DISCLOSURE_FACT_LABELS,
    NONRECURRING_DISCLOSURE_PARSER_VERSION,
    ROLE_BY_FACT,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parts(root: Path, filename: str) -> pd.DataFrame:
    paths = sorted(root.rglob(filename))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(path, dtype={"entity_id": str}) for path in paths], ignore_index=True, sort=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--require-no-hard-failures", action="store_true")
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "INVESTMENT_DECISION_CHAIN_NONRECURRING_DISCLOSURE_V1":
        raise ValueError("unexpected non-recurring disclosure contract")
    if contract.get("parser", {}).get("parser_version") != NONRECURRING_DISCLOSURE_PARSER_VERSION:
        raise ValueError("non-recurring disclosure parser version drift")
    if contract.get("parser", {}).get("fact_types") != list(NONRECURRING_DISCLOSURE_FACT_LABELS):
        raise ValueError("non-recurring disclosure fact inventory drift")

    scope = pd.read_csv(args.scope_csv, dtype=str)
    symbols = sorted({str(value).zfill(6) for value in scope["symbol"].dropna()})
    if len(symbols) != int(contract["scope"]["symbol_count"]):
        raise ValueError("scope symbol count drift")
    if _sha(args.scope_csv) != contract["scope"]["sha256"]:
        raise ValueError("scope hash drift")

    facts = _load_parts(args.shard_root, "nonrecurring_disclosure_facts.csv")
    coverage = _load_parts(args.shard_root, "coverage.csv")
    errors = _load_parts(args.shard_root, "errors.csv")
    manifests = sorted(args.shard_root.rglob("stage_manifest.json"))
    if not manifests:
        raise ValueError("no shard manifests")
    manifest_rows = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
    expected_shards = int(contract.get("execution", {}).get("full_shards", 0) or 0)
    shard_indices = [int(item.get("shard_index")) for item in manifest_rows]
    if expected_shards <= 0:
        raise ValueError("full shard count missing from contract")
    if len(manifests) != expected_shards or sorted(shard_indices) != list(range(expected_shards)):
        raise ValueError("shard manifest identity/completeness drift")
    shard_source_commits = {str(int(item["shard_index"])): str(item.get("source_commit") or "") for item in manifest_rows}
    if any(len(value) != 40 for value in shard_source_commits.values()):
        raise ValueError("shard source commit provenance missing")
    source_commits = sorted(set(shard_source_commits.values()))

    expected_facts = set(NONRECURRING_DISCLOSURE_FACT_LABELS)
    if len(facts):
        required_cols = {"disclosure_role", "source_row_label"}
        if not required_cols.issubset(facts.columns):
            raise ValueError("non-recurring disclosure raw-row metadata missing")
        if not set(facts["fact_type"].astype(str)).issubset(expected_facts):
            raise ValueError("unexpected fact type in aggregate")
        role_drift = facts.loc[
            facts.apply(lambda row: str(row["disclosure_role"]) != ROLE_BY_FACT[str(row["fact_type"])], axis=1)
        ]
        if len(role_drift):
            raise ValueError("non-recurring disclosure role drift")
        facts = (
            facts.drop_duplicates(["entity_id", "document_id", "revision_id", "fact_type"], keep="last")
            .sort_values(["entity_id", "period_end", "evidence_available_date", "document_id", "fact_type"])
            .reset_index(drop=True)
        )
    if len(coverage):
        coverage = coverage.drop_duplicates(["entity_id", "coverage_start", "coverage_end"], keep="last").sort_values("entity_id")
    if len(errors):
        errors = errors.drop_duplicates().sort_values(["entity_id", "document_id"])

    hard_failures = int((errors["severity"] == "HARD_FAILURE").sum()) if len(errors) and "severity" in errors.columns else 0
    if args.require_no_hard_failures and hard_failures:
        raise ValueError(f"hard failures remain: {hard_failures}")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    fact_path = out / "nonrecurring_disclosure_facts.csv"
    coverage_path = out / "coverage.csv"
    error_path = out / "errors.csv"
    facts.to_csv(fact_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    errors.to_csv(error_path, index=False)

    entity_counts = {
        fact: int(facts.loc[facts["fact_type"].eq(fact), "entity_id"].nunique()) if len(facts) else 0
        for fact in NONRECURRING_DISCLOSURE_FACT_LABELS
    }
    manifest = {
        "schema_version": "investment-decision-nonrecurring-disclosure-bundle-v1",
        "status": "PUBLIC_OUTCOME_BLIND_RAW_EXPLICIT_NONRECURRING_DISCLOSURE_BUNDLE",
        "source_contract_id": contract["contract_id"],
        "decision_date": contract["decision_date"],
        "scope_symbol_count": len(symbols),
        "shard_manifest_count": len(manifests),
        "shard_indices": sorted(shard_indices),
        "source_commits": source_commits,
        "shard_source_commits": shard_source_commits,
        "fact_types": list(NONRECURRING_DISCLOSURE_FACT_LABELS),
        "fact_entity_counts": entity_counts,
        "hard_failure_rows": hard_failures,
        "files": {
            "nonrecurring_disclosure_facts.csv": _sha(fact_path),
            "coverage.csv": _sha(coverage_path),
            "errors.csv": _sha(error_path),
        },
        "historical_forward_outcome_read": False,
        "prospective_forward_outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "private_model_semantics_included": False,
        "synthetic_unusual_aggregate_included": False,
        "missing_values_zero_imputed": False,
    }
    (out / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
