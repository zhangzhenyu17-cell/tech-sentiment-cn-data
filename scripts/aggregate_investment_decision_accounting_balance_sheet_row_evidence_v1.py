from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parts(root: Path, filename: str) -> pd.DataFrame:
    paths = sorted(root.rglob(filename))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(path, dtype={"entity_id": str}, keep_default_na=False) for path in paths], ignore_index=True, sort=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--require-no-hard-failures", action="store_true")
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "INVESTMENT_DECISION_CHAIN_ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_V1":
        raise ValueError("unexpected accounting balance-sheet row-evidence contract")
    fact_types = tuple(str(value) for value in contract["parser"]["fact_types"])
    if len(fact_types) != int(contract["parser"]["fact_type_count"]):
        raise ValueError("fact-type count drift")
    scope = pd.read_csv(args.scope_csv, dtype=str)
    symbols = sorted({str(value).zfill(6) for value in scope["symbol"].dropna()})
    if len(symbols) != int(contract["scope"]["symbol_count"]):
        raise ValueError("scope symbol count drift")
    if _sha(args.scope_csv) != contract["scope"]["sha256"]:
        raise ValueError("scope hash drift")

    facts = _load_parts(args.shard_root, "accounting_balance_sheet_row_evidence.csv")
    coverage = _load_parts(args.shard_root, "coverage.csv")
    errors = _load_parts(args.shard_root, "errors.csv")
    manifests = sorted(args.shard_root.rglob("stage_manifest.json"))
    if not manifests:
        raise ValueError("no shard manifests")
    manifest_rows = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
    expected_shards = int(contract["execution"]["full_shards"])
    shard_indices = [int(item["shard_index"]) for item in manifest_rows]
    if len(manifests) != expected_shards or sorted(shard_indices) != list(range(expected_shards)):
        raise ValueError("shard manifest identity/completeness drift")
    if any(item.get("dash_or_blank_interpreted_as_zero") is not False for item in manifest_rows):
        raise ValueError("shard row-evidence zero interpretation drift")
    if any(item.get("private_operating_financing_classification_included") is not False for item in manifest_rows):
        raise ValueError("shard row-evidence private semantics drift")
    shard_source_commits = {str(int(item["shard_index"])): str(item.get("source_commit") or "") for item in manifest_rows}
    if any(len(value) != 40 for value in shard_source_commits.values()):
        raise ValueError("shard source commit provenance missing")
    source_commits = sorted(set(shard_source_commits.values()))

    required = {
        "entity_id", "period_end", "fact_type", "document_id", "revision_id", "document_sha256",
        "source_row_label", "statement_unit", "row_layout_state", "current_cell_kind",
        "current_cell_token", "current_value_cny", "prior_cell_kind", "prior_cell_token",
        "source_row_sha256", "zero_interpretation_applied", "private_classification_applied",
    }
    if len(facts):
        missing = required - set(facts.columns)
        if missing:
            raise ValueError(f"row-evidence aggregate columns missing: {sorted(missing)}")
        facts = facts.drop_duplicates(["entity_id", "document_id", "revision_id", "fact_type"], keep="last").sort_values(
            ["entity_id", "period_end", "evidence_available_date", "document_id", "fact_type"]
        )
        if not set(facts["fact_type"].astype(str)).issubset(set(fact_types)):
            raise ValueError("unexpected fact type in row-evidence aggregate")
        if facts["zero_interpretation_applied"].astype(str).str.lower().isin({"true", "1"}).any():
            raise ValueError("row-evidence aggregate zero interpretation drift")
        if facts["private_classification_applied"].astype(str).str.lower().isin({"true", "1"}).any():
            raise ValueError("row-evidence aggregate private classification drift")
        dash = facts["current_cell_kind"].astype(str).eq("DASH")
        if dash.any() and facts.loc[dash, "current_value_cny"].astype(str).str.strip().ne("").any():
            raise ValueError("row-evidence DASH current cell leaked numeric value")
    if len(coverage):
        coverage = coverage.drop_duplicates(["entity_id", "coverage_start", "coverage_end"], keep="last").sort_values("entity_id")
    if len(errors):
        errors = errors.drop_duplicates().sort_values(["entity_id", "document_id"])
    hard_failures = int((errors["severity"] == "HARD_FAILURE").sum()) if len(errors) and "severity" in errors.columns else 0
    if args.require_no_hard_failures and hard_failures:
        raise ValueError(f"hard failures remain: {hard_failures}")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    evidence_path = out / "accounting_balance_sheet_row_evidence.csv"
    coverage_path = out / "coverage.csv"
    errors_path = out / "errors.csv"
    facts.to_csv(evidence_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    errors.to_csv(errors_path, index=False)
    fact_entity_counts = {
        fact: int(facts.loc[facts["fact_type"].eq(fact), "entity_id"].nunique()) if len(facts) else 0
        for fact in fact_types
    }
    cell_kind_counts = facts["current_cell_kind"].replace("", "NONE").value_counts().sort_index().to_dict() if len(facts) else {}
    manifest = {
        "schema_version": "investment-decision-accounting-balance-sheet-row-evidence-bundle-v1",
        "status": "PUBLIC_OUTCOME_BLIND_RAW_BALANCE_SHEET_ROW_EVIDENCE_BUNDLE",
        "source_contract_id": contract["contract_id"],
        "decision_date": contract["decision_date"],
        "scope_symbol_count": len(symbols),
        "shard_manifest_count": len(manifests),
        "shard_indices": sorted(shard_indices),
        "source_commits": source_commits,
        "shard_source_commits": shard_source_commits,
        "fact_types": list(fact_types),
        "fact_entity_counts": fact_entity_counts,
        "current_cell_kind_counts": {str(k): int(v) for k, v in cell_kind_counts.items()},
        "row_evidence_count": int(len(facts)),
        "hard_failure_rows": hard_failures,
        "files": {
            "accounting_balance_sheet_row_evidence.csv": _sha(evidence_path),
            "coverage.csv": _sha(coverage_path),
            "errors.csv": _sha(errors_path),
        },
        "dash_or_blank_interpreted_as_zero": False,
        "private_model_semantics_included": False,
        "historical_forward_outcome_read": False,
        "prospective_forward_outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (out / "bundle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
