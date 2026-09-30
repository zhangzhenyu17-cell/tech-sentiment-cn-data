from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = "cross-sector-fundamental-expansion-shard-a-bundle-v1"
WORK_UNIT_SCHEMA = "cross-sector-fundamental-expansion-shard-a-work-unit-v1"
DECISION_DATE = "2026-09-29"
SHARD_ID = "A"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_many(root: Path, name: str) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob(name)):
        try:
            frame = pd.read_csv(path, dtype={"symbol": str})
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        if not frame.empty:
            frame = frame.copy()
            frame["_source_file"] = path.as_posix()
            frames.append(frame)
    return frames


def _truth(value: object) -> bool:
    return str(value).strip().lower() == "true"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate CSRM Fundamental Expansion Shard A work units without outcome access."
    )
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--scope-contract", type=Path, required=True)
    parser.add_argument("--work-unit-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    scope = pd.read_csv(args.scope_csv, dtype={"symbol": str})
    contract = json.loads(args.scope_contract.read_text(encoding="utf-8"))
    if len(scope) != 219 or contract.get("shard_id") != SHARD_ID:
        raise ValueError("Shard A scope contract drift")
    if _sha256(args.scope_csv) != contract.get("scope_csv_sha256"):
        raise ValueError("Shard A scope checksum drift")
    if set(scope["shard_id"].astype(str)) != {SHARD_ID}:
        raise ValueError("Shard A id drift")
    expected = set(scope["entity_id"].astype(str))

    manifests = []
    observed: set[str] = set()
    duplicate_observed: set[str] = set()
    for path in sorted(args.work_unit_root.rglob("stage_manifest.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema_version") != WORK_UNIT_SCHEMA:
            continue
        if item.get("parent_shard_id") != SHARD_ID or item.get("decision_date") != DECISION_DATE:
            raise ValueError("work-unit identity drift")
        if item.get("outcome_read") is not False:
            raise ValueError("outcome boundary drift")
        for entity_id in item.get("entity_ids", []):
            if entity_id in observed:
                duplicate_observed.add(entity_id)
            observed.add(entity_id)
        manifests.append(item)
    if duplicate_observed:
        raise ValueError(f"duplicate work-unit entities: {sorted(duplicate_observed)[:10]}")
    if observed - expected:
        raise ValueError(f"work-unit scope contains extras: {sorted(observed - expected)[:10]}")

    evidence_parts = _read_many(args.work_unit_root, "fundamental_state_evidence.csv")
    coverage_parts = _read_many(args.work_unit_root, "fundamental_state_coverage.csv")
    filing_parts = _read_many(args.work_unit_root, "versioned_filing_facts.csv")
    filing_coverage_parts = _read_many(args.work_unit_root, "filing_coverage.csv")
    error_parts = _read_many(args.work_unit_root, "filing_errors.csv")
    receipt_parts = _read_many(args.work_unit_root, "entity_readiness.csv")

    evidence = pd.concat(evidence_parts, ignore_index=True) if evidence_parts else pd.DataFrame()
    coverage = pd.concat(coverage_parts, ignore_index=True) if coverage_parts else pd.DataFrame()
    filings = pd.concat(filing_parts, ignore_index=True) if filing_parts else pd.DataFrame()
    filing_coverage = pd.concat(filing_coverage_parts, ignore_index=True) if filing_coverage_parts else pd.DataFrame()
    errors = pd.concat(error_parts, ignore_index=True) if error_parts else pd.DataFrame()
    receipts = pd.concat(receipt_parts, ignore_index=True) if receipt_parts else pd.DataFrame()

    if not evidence.empty:
        forbidden = [
            c for c in evidence.columns
            if any(token in str(c).lower() for token in ("forward_return", "outcome", "mfe", "mae"))
        ]
        if forbidden:
            raise ValueError(f"outcome-like columns forbidden: {forbidden}")
        if "evidence_id" in evidence.columns:
            evidence = evidence.drop_duplicates("evidence_id")

    receipt_map: dict[str, dict[str, str]] = {}
    if not receipts.empty:
        for row in receipts.to_dict("records"):
            entity_id = str(row["entity_id"])
            if entity_id in receipt_map:
                raise ValueError(f"duplicate readiness receipt for {entity_id}")
            receipt_map[entity_id] = {
                "readiness": str(row.get("readiness", "MISSING")),
                "failure_reason": str(row.get("failure_reason", "") or ""),
            }

    final_rows = []
    for row in scope.sort_values("entity_id").to_dict("records"):
        entity_id = str(row["entity_id"])
        if entity_id in receipt_map:
            state = receipt_map[entity_id]["readiness"]
            reason = receipt_map[entity_id]["failure_reason"]
        else:
            state = "MISSING"
            reason = "WORK_UNIT_OUTPUT_MISSING_OR_FAILED"
        if state != "READY" and not reason:
            reason = "UNSPECIFIED_FAIL_CLOSED"
        final_rows.append(
            {
                **row,
                "readiness": state,
                "failure_reason": reason,
            }
        )
    readiness = pd.DataFrame(final_rows)
    ready = readiness[readiness["readiness"].eq("READY")].copy()
    missing = readiness[~readiness["readiness"].eq("READY")].copy()

    contributions = {}
    for key, col in (
        ("931152", "in_innovation_drug"),
        ("399973", "in_defense"),
        ("000510", "in_core_beta"),
    ):
        in_domain = readiness[col].map(_truth)
        contributions[key] = {
            "shard_a_scope_entities_in_domain": int(in_domain.sum()),
            "new_ready_entities_from_shard_a": int((in_domain & readiness["readiness"].eq("READY")).sum()),
            "explicit_missing_entities_from_shard_a": int((in_domain & ~readiness["readiness"].eq("READY")).sum()),
        }

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "combined_versioned_filing_facts.csv": filings,
        "combined_fundamental_state_evidence.csv": evidence,
        "combined_fundamental_state_coverage.csv": coverage,
        "combined_filing_coverage.csv": filing_coverage,
        "combined_filing_errors.csv": errors,
        "entity_readiness.csv": readiness,
    }
    for name, frame in outputs.items():
        frame.to_csv(out / name, index=False)

    terminal = (
        "SHARD_READY_FOR_FINAL_INTEGRATION"
        if len(missing) == 0
        else "SHARD_PARTIAL_WITH_EXPLICIT_MISSING_ENTITIES"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": terminal,
        "shard_id": SHARD_ID,
        "scope_entities": 219,
        "observed_work_unit_entities": len(observed),
        "work_unit_manifests": len(manifests),
        "ready_entities": len(ready),
        "explicit_missing_entities": len(missing),
        "domain_new_coverage_contribution": contributions,
        "decision_date": DECISION_DATE,
        "source_commit": args.source_commit,
        "outcome_read": False,
        "historical_outcome_read": False,
        "prospective_outcome_read": False,
        "parameter_search_run": False,
        "threshold_search_run": False,
        "weight_search_run": False,
        "ml_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "pairwise_builder_run": False,
        "final_integration_run": False,
    }
    (out / "aggregate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    files = {}
    for path in sorted(out.iterdir()):
        if path.is_file():
            files[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": terminal,
        "shard_id": SHARD_ID,
        "scope_csv_path": args.scope_csv.as_posix(),
        "scope_csv_sha256": _sha256(args.scope_csv),
        "scope_contract_path": args.scope_contract.as_posix(),
        "scope_contract_sha256": _sha256(args.scope_contract),
        "source_commit": args.source_commit,
        "parser_identity": f"tech_sentiment.filing_materialization@{args.source_commit}",
        "state_builder_identity": f"tech_sentiment.fundamental_pit_state@{args.source_commit}",
        "decision_date": DECISION_DATE,
        "exact_entity_ids": scope.sort_values("entity_id")["entity_id"].astype(str).tolist(),
        "domain_new_coverage_contribution": contributions,
        "files": files,
        "authority": {
            "outcome_read": False,
            "evidence_qualification_changed": False,
            "production_changed": False,
            "trading_authority_changed": False,
            "pairwise_builder_run": False,
            "final_integration_run": False,
        },
    }
    (out / "bundle_manifest.json").write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
