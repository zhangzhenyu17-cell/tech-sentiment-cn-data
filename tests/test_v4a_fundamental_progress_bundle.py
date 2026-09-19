from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.v4a_persistent_stage import PERSISTENT_STAGE_SCHEMA
from tech_sentiment.v4a_stage_artifact import (
    build_stage_receipt,
    write_stage_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "v4a_fundamental_progress_bundle.py"
_SPEC = importlib.util.spec_from_file_location("v4a_fundamental_progress_bundle_test", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path):
    symbols = [f"{value:06d}" for value in range(1, 18)]
    symbols_csv = tmp_path / "symbols.csv"
    pd.DataFrame({"symbol": symbols}).to_csv(symbols_csv, index=False)

    shared_manifest = tmp_path / "shared.manifest.json"
    _write_json(
        shared_manifest,
        {
            "schema_version": PERSISTENT_STAGE_SCHEMA,
            "family": "shared",
            "stage_id": "shared",
            "bundle_identity": "shared-bundle-fixture",
            "compatibility_key": "shared-compat-fixture",
            "archive_sha256": "a" * 64,
        },
    )

    stage = tmp_path / "stage"
    stage.mkdir()
    selected = _MODULE._symbols_for_unit(
        symbols_csv,
        unit_index=0,
        unit_count=16,
    )
    _write_json(
        stage / "stage_manifest.json",
        {
            "schema_version": "v4a-fundamental-earnings-shard-v1",
            "source_commit": "f" * 40,
            "start_date": "2022-01-04",
            "end_date": "2026-09-17",
            "shard_index": 0,
            "shard_count": 16,
            "symbols": selected,
        },
    )
    pd.DataFrame(
        [{"entity_id": "000001.SZ", "query_status": "COMPLETE_WINDOW"}]
    ).to_csv(stage / "filing_coverage.csv", index=False)

    payload_files = [
        path
        for path in stage.rglob("*")
        if path.is_file() and path.name != "receipt.json"
    ]
    receipt = build_stage_receipt(
        root=stage,
        files=payload_files,
        stage_kind="fundamental_earnings",
        stage_id="fundamental-0-of-16",
        source_commit="f" * 40,
        start_date="2022-01-04",
        end_date="2026-09-17",
    )
    write_stage_receipt(stage / "receipt.json", receipt)
    return symbols_csv, shared_manifest, stage, selected


def test_progress_bundle_round_trip_is_semantic_and_not_formal_evidence(tmp_path: Path):
    symbols_csv, shared_manifest, stage, selected = _fixture(tmp_path)
    out = tmp_path / "dist"

    manifest = _MODULE.package(
        repo_root=ROOT,
        stage_root=stage,
        symbols_csv=symbols_csv,
        shared_manifest=shared_manifest,
        contract_path=ROOT / "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
        start_date="2022-01-04",
        end_date="2026-09-17",
        unit_index=0,
        unit_count=16,
        out_dir=out,
    )

    assert manifest["formal_evidence_handoff"] is False
    assert manifest["requires_full_group_assembly"] is True
    assert manifest["partial_progress_never_grants_qualification"] is True
    assert manifest["symbols"] == selected
    semantic_paths = {row["path"] for row in manifest["semantic_files"]}
    assert ".github/workflows/v4a-fundamental-earnings.yml" not in semantic_paths
    assert "src/tech_sentiment/official_filing_facts.py" in semantic_paths

    base = manifest["asset_base"]
    restored = tmp_path / "restored"
    verified = _MODULE.verify(
        repo_root=ROOT,
        archive_path=out / f"{base}.tar.gz",
        manifest_path=out / f"{base}.manifest.json",
        symbols_csv=symbols_csv,
        shared_manifest=shared_manifest,
        contract_path=ROOT / "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
        start_date="2022-01-04",
        end_date="2026-09-17",
        unit_index=0,
        unit_count=16,
        extract_to=restored,
    )
    assert verified["bundle_identity"] == manifest["bundle_identity"]
    assert (restored / "receipt.json").is_file()
    assert (restored / "stage_manifest.json").is_file()


def test_progress_bundle_rejects_symbol_scope_or_unit_identity_drift(tmp_path: Path):
    symbols_csv, shared_manifest, stage, _ = _fixture(tmp_path)
    out = tmp_path / "dist"
    manifest = _MODULE.package(
        repo_root=ROOT,
        stage_root=stage,
        symbols_csv=symbols_csv,
        shared_manifest=shared_manifest,
        contract_path=ROOT / "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
        start_date="2022-01-04",
        end_date="2026-09-17",
        unit_index=0,
        unit_count=16,
        out_dir=out,
    )
    base = manifest["asset_base"]

    changed_scope = tmp_path / "changed-symbols.csv"
    pd.DataFrame({"symbol": ["000001", "000002"]}).to_csv(changed_scope, index=False)
    with pytest.raises(ValueError, match="compatibility mismatch"):
        _MODULE.verify(
            repo_root=ROOT,
            archive_path=out / f"{base}.tar.gz",
            manifest_path=out / f"{base}.manifest.json",
            symbols_csv=changed_scope,
            shared_manifest=shared_manifest,
            contract_path=ROOT / "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
            start_date="2022-01-04",
            end_date="2026-09-17",
            unit_index=0,
            unit_count=16,
            extract_to=tmp_path / "bad-restore",
        )


def test_progress_contract_refuses_wrong_unit_count(tmp_path: Path):
    symbols_csv, shared_manifest, _, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="unit-count mismatch"):
        _MODULE.descriptor(
            repo_root=ROOT,
            symbols_csv=symbols_csv,
            shared_manifest=shared_manifest,
            contract_path=ROOT / "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
            start_date="2022-01-04",
            end_date="2026-09-17",
            unit_index=0,
            unit_count=4,
        )
