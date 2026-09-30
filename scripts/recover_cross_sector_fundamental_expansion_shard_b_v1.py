from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

WORK_UNIT_SCHEMA = "cross-sector-fundamental-expansion-shard-b-work-unit-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _artifact_dir(root: Path, name: str) -> Path:
    direct = root / name
    if direct.is_dir():
        return direct
    if (root / "stage_manifest.json").is_file() and root.name == name:
        return root
    hits = [p for p in root.rglob(name) if p.is_dir()]
    if len(hits) != 1:
        raise ValueError(f"artifact directory identity mismatch for {name}: {len(hits)} hits")
    return hits[0]


def _validate_manifest(
    *,
    manifest: dict[str, Any],
    unit: int,
    expected_entities: list[str],
    artifact: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    _require(manifest.get("schema_version") == WORK_UNIT_SCHEMA, f"unit {unit} schema drift")
    _require(manifest.get("parent_shard_id") == "B", f"unit {unit} shard drift")
    _require(manifest.get("decision_date") == contract["decision_date"], f"unit {unit} decision date drift")
    _require(int(manifest.get("work_unit_index", -1)) == unit, f"unit {unit} index drift")
    _require(int(manifest.get("work_unit_count", -1)) == contract["work_unit_count"], f"unit {unit} count drift")
    _require(manifest.get("source_commit") == artifact["source_commit"], f"unit {unit} source commit drift")
    _require(manifest.get("entity_ids") == expected_entities, f"unit {unit} entity partition drift")

    _require(
        manifest.get("filing_materializer_version") == contract["expected_filing_materializer_version"],
        f"unit {unit} filing materializer drift",
    )
    _require(
        manifest.get("filing_parser_version") == contract["expected_filing_parser_version"],
        f"unit {unit} filing parser drift",
    )
    _require(
        manifest.get("fundamental_state_contract_id") == contract["expected_fundamental_state_contract_id"],
        f"unit {unit} state contract drift",
    )
    _require(
        manifest.get("fundamental_state_formula_version") == contract["expected_fundamental_state_formula_version"],
        f"unit {unit} state formula drift",
    )
    for key in (
        "outcome_read",
        "historical_outcome_read",
        "prospective_outcome_read",
        "parameter_search_run",
        "threshold_search_run",
        "weight_search_run",
        "ml_run",
        "evidence_qualification_changed",
        "production_changed",
        "trading_authority_changed",
    ):
        _require(manifest.get(key) is False, f"unit {unit} authority drift: {key}")


def recover_work_units(
    *,
    scope_csv: Path,
    recovery_contract: Path,
    original_root: Path,
    repair_root: Path,
    out_root: Path,
) -> dict[str, Any]:
    contract = _read_json(recovery_contract)
    _require(contract.get("contract_id") == "CROSS_SECTOR_FUNDAMENTAL_EXPANSION_SHARD_B_RECOVERY_V1", "recovery contract id drift")
    _require(contract.get("status") == "FROZEN_OUTCOME_BLIND_SHARD_B_RECOVERY_ONLY", "recovery contract status drift")
    _require(all(value is False for value in contract["authority"].values()), "recovery authority drift")
    _require(_sha256(scope_csv) == contract["scope_csv_sha256"], "scope checksum drift")

    scope = pd.read_csv(scope_csv, dtype={"symbol": str})
    _require(len(scope) == contract["scope_entities"] == 218, "scope entity count drift")
    units = int(contract["work_unit_count"])
    repair_unit = int(contract["repair_unit"])
    expected_by_unit = {
        unit: [
            str(row["entity_id"])
            for index, row in scope.iterrows()
            if index % units == unit
        ]
        for unit in range(units)
    }

    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    manifests: dict[str, dict[str, Any]] = {}

    artifacts = contract["work_unit_artifacts"]
    _require(set(map(int, artifacts)) == set(range(units)), "recovery artifact unit set drift")

    for unit in range(units):
        artifact = artifacts[str(unit)]
        source_root = repair_root if unit == repair_unit else original_root
        expected_run = contract["repair_run_id"] if unit == repair_unit else contract["original_run_id"]
        _require(int(artifact["source_run_id"]) == int(expected_run), f"unit {unit} source run drift")
        name = f"csrm-fundamental-expansion-shard-b-unit-{unit}"
        _require(artifact["name"] == name, f"unit {unit} artifact name drift")
        src = _artifact_dir(source_root, name)
        manifest = _read_json(src / "stage_manifest.json")
        _validate_manifest(
            manifest=manifest,
            unit=unit,
            expected_entities=expected_by_unit[unit],
            artifact=artifact,
            contract=contract,
        )
        dest = out_root / name
        shutil.copytree(src, dest)
        manifests[str(unit)] = {
            "source_run_id": int(artifact["source_run_id"]),
            "artifact_id": int(artifact["artifact_id"]),
            "artifact_digest": artifact["digest"],
            "source_commit": artifact["source_commit"],
            "entity_count": len(expected_by_unit[unit]),
        }

    observed = sum(item["entity_count"] for item in manifests.values())
    _require(observed == 218, "recovery entity accounting drift")

    receipt = {
        "schema_version": "cross-sector-fundamental-expansion-shard-b-recovery-receipt-v1",
        "contract_id": contract["contract_id"],
        "shard_id": "B",
        "decision_date": contract["decision_date"],
        "scope_entities": 218,
        "work_unit_count": units,
        "repair_unit": repair_unit,
        "work_unit_provenance": manifests,
        "work_unit_source_commits": sorted({item["source_commit"] for item in manifests.values()}),
        "outcome_read": False,
        "pairwise_builder_run": False,
        "final_a_b_integration_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    (out_root / "recovery_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover exact Shard B work units from pinned original and repair artifacts.")
    parser.add_argument("--scope-csv", type=Path, required=True)
    parser.add_argument("--recovery-contract", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--repair-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    receipt = recover_work_units(
        scope_csv=args.scope_csv,
        recovery_contract=args.recovery_contract,
        original_root=args.original_root,
        repair_root=args.repair_root,
        out_root=args.out_root,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
