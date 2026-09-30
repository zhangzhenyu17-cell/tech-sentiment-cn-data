from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd
import pytest

from scripts.recover_cross_sector_fundamental_expansion_shard_b_v1 import recover_work_units
from scripts.validate_cross_sector_fundamental_expansion_shard_b_recovery_artifacts_v1 import (
    validate_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "data/reference/cross_sector_fundamental_expansion_shard_b_scope.csv"
SCOPE_CONTRACT = ROOT / "reference/cross_sector_fundamental_expansion_shard_b_v1.json"
RECOVERY_CONTRACT = ROOT / "reference/cross_sector_fundamental_expansion_shard_b_recovery_v1.json"
WORKFLOW = ROOT / ".github/workflows/cross-sector-fundamental-expansion-shard-b-recovery-v1.yml"
AGGREGATE = ROOT / "scripts/aggregate_cross_sector_fundamental_expansion_shard_b_v1.py"

MISSING = {
    "000039.SZ",
    "002594.SZ",
    "600036.SH",
    "601288.SH",
    "601319.SH",
    "601398.SH",
    "601988.SH",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract() -> dict:
    return json.loads(RECOVERY_CONTRACT.read_text(encoding="utf-8"))


def _write_fake_artifacts(tmp_path: Path) -> tuple[Path, Path]:
    contract = _contract()
    scope = pd.read_csv(SCOPE, dtype={"symbol": str})
    original = tmp_path / "original"
    repair = tmp_path / "repair"
    original.mkdir()
    repair.mkdir()
    units = int(contract["work_unit_count"])

    for unit in range(units):
        artifact = contract["work_unit_artifacts"][str(unit)]
        root = repair if unit == int(contract["repair_unit"]) else original
        out = root / artifact["name"]
        out.mkdir()
        entities = [
            str(row["entity_id"])
            for index, row in scope.iterrows()
            if index % units == unit
        ]
        manifest = {
            "schema_version": "cross-sector-fundamental-expansion-shard-b-work-unit-v1",
            "parent_shard_id": "B",
            "decision_date": "2026-09-29",
            "work_unit_index": unit,
            "work_unit_count": 32,
            "source_commit": artifact["source_commit"],
            "entity_ids": entities,
            "filing_materializer_version": contract["expected_filing_materializer_version"],
            "filing_parser_version": contract["expected_filing_parser_version"],
            "fundamental_state_contract_id": contract["expected_fundamental_state_contract_id"],
            "fundamental_state_formula_version": contract["expected_fundamental_state_formula_version"],
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
        }
        (out / "stage_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (out / "entity_readiness.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["entity_id", "readiness", "failure_reason"],
            )
            writer.writeheader()
            for entity_id in entities:
                is_missing = entity_id in MISSING
                writer.writerow(
                    {
                        "entity_id": entity_id,
                        "readiness": "MISSING" if is_missing else "READY",
                        "failure_reason": (
                            "SYNTHETIC_EXPLICIT_DATA_INSUFFICIENCY" if is_missing else ""
                        ),
                    }
                )
    return original, repair


def test_recovery_contract_pins_exact_runs_artifacts_and_boundaries() -> None:
    contract = _contract()
    assert contract["contract_id"] == "CROSS_SECTOR_FUNDAMENTAL_EXPANSION_SHARD_B_RECOVERY_V1"
    assert contract["status"] == "FROZEN_OUTCOME_BLIND_SHARD_B_RECOVERY_ONLY"
    assert contract["original_run_id"] == 36739370288
    assert contract["repair_run_id"] == 36752822055
    assert contract["repair_unit"] == 12
    assert contract["scope_entities"] == 218
    assert contract["scope_csv_sha256"] == _sha(SCOPE)
    assert set(map(int, contract["work_unit_artifacts"])) == set(range(32))
    assert contract["work_unit_artifacts"]["12"]["artifact_id"] == 11115828598
    assert contract["work_unit_artifacts"]["12"]["digest"] == (
        "sha256:a28dace767d5a9151bde1834b9bf3b75661f8f60a27fc10fe897b573e8e752db"
    )
    assert all(value is False for value in contract["authority"].values())


def test_recovery_metadata_validator_requires_exact_artifact_ids_and_digests(tmp_path: Path) -> None:
    contract = _contract()
    original_rows = []
    repair_rows = []
    for unit_text, item in contract["work_unit_artifacts"].items():
        row = {
            "id": item["artifact_id"],
            "name": item["name"],
            "digest": item["digest"],
            "expired": False,
        }
        (repair_rows if int(unit_text) == 12 else original_rows).append(row)

    original_json = tmp_path / "original.json"
    repair_json = tmp_path / "repair.json"
    original_json.write_text(json.dumps({"artifacts": original_rows}), encoding="utf-8")
    repair_json.write_text(json.dumps({"artifacts": repair_rows}), encoding="utf-8")

    result = validate_metadata(
        contract_path=RECOVERY_CONTRACT,
        original_json=original_json,
        repair_json=repair_json,
    )
    assert result["checked_artifacts"] == 32

    repair_rows[0]["digest"] = "sha256:" + "0" * 64
    repair_json.write_text(json.dumps({"artifacts": repair_rows}), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact digest drift"):
        validate_metadata(
            contract_path=RECOVERY_CONTRACT,
            original_json=original_json,
            repair_json=repair_json,
        )


def test_recovery_assembles_exact_218_and_aggregates_211_ready_7_missing(tmp_path: Path) -> None:
    original, repair = _write_fake_artifacts(tmp_path)
    work_units = tmp_path / "work-units"
    receipt = recover_work_units(
        scope_csv=SCOPE,
        recovery_contract=RECOVERY_CONTRACT,
        original_root=original,
        repair_root=repair,
        out_root=work_units,
    )
    assert receipt["scope_entities"] == 218
    assert receipt["work_unit_count"] == 32
    assert receipt["repair_unit"] == 12
    assert receipt["work_unit_source_commits"] == [
        "5760854c070e288855208de921adf64ac348d287",
        "a27478e56a76d978034d78fe32b6fe0e2fa666f3",
    ]
    assert receipt["final_a_b_integration_run"] is False

    out = tmp_path / "final"
    aggregation_commit = "f" * 40
    subprocess.run(
        [
            sys.executable,
            str(AGGREGATE),
            "--scope-csv",
            str(SCOPE),
            "--scope-contract",
            str(SCOPE_CONTRACT),
            "--work-unit-root",
            str(work_units),
            "--source-commit",
            aggregation_commit,
            "--recovery-contract",
            str(RECOVERY_CONTRACT),
            "--out-dir",
            str(out),
        ],
        check=True,
        cwd=ROOT,
    )

    summary = json.loads((out / "aggregate_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert summary["observed_work_unit_entities"] == 218
    assert summary["work_unit_manifests"] == 32
    assert summary["ready_entities"] == 211
    assert summary["explicit_missing_entities"] == 7
    assert summary["terminal_state"] == "SHARD_PARTIAL_WITH_EXPLICIT_MISSING_ENTITIES"
    assert summary["shard_recovery_integration_run"] is True
    assert summary["final_integration_run"] is False
    assert manifest["source_commit"] == aggregation_commit
    assert manifest["parser_identity"] == "RECOVERED_PINNED_WORK_UNIT_SET"
    assert manifest["state_builder_identity"] == "RECOVERED_PINNED_WORK_UNIT_SET"
    assert manifest["work_unit_source_commits"] == receipt["work_unit_source_commits"]
    assert manifest["recovery_original_run_id"] == 36739370288
    assert manifest["recovery_repair_run_id"] == 36752822055
    assert manifest["recovery_repair_unit"] == 12
    assert manifest["authority"]["final_integration_run"] is False
    assert (out / "recovery_receipt.json").is_file()
    assert (out / "recovery_contract.json").is_file()


def test_recovery_rejects_work_unit_source_commit_drift(tmp_path: Path) -> None:
    original, repair = _write_fake_artifacts(tmp_path)
    manifest_path = repair / "csrm-fundamental-expansion-shard-b-unit-12" / "stage_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_commit"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source commit drift"):
        recover_work_units(
            scope_csv=SCOPE,
            recovery_contract=RECOVERY_CONTRACT,
            original_root=original,
            repair_root=repair,
            out_root=tmp_path / "work-units",
        )


def test_recovery_workflow_is_manual_only_and_does_not_recompute() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:", "repository_dispatch:"):
        assert trigger not in text
    assert 'ORIGINAL_RUN_ID: "36739370288"' in text
    assert 'REPAIR_RUN_ID: "36752822055"' in text
    assert 'REPAIR_UNIT: "12"' in text
    assert "actions: read" in text
    assert "validate_cross_sector_fundamental_expansion_shard_b_recovery_artifacts_v1.py" in text
    assert "recover_cross_sector_fundamental_expansion_shard_b_v1.py" in text
    assert "aggregate_cross_sector_fundamental_expansion_shard_b_v1.py" in text
    assert "materialize_cross_sector_fundamental_expansion_shard_b_v1.py" not in text
    assert 'assert s["ready_entities"] == 211' in text
    assert 'assert s["explicit_missing_entities"] == 7' in text
    assert "cross-sector-fundamental-expansion-shard-b-v1" in text
