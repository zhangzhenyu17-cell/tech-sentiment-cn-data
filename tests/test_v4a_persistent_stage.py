from __future__ import annotations

import json
from pathlib import Path
import tarfile

import pytest

import tech_sentiment.v4a_persistent_stage as persistent
from tech_sentiment.v4a_persistent_stage import StageSpec
from tech_sentiment.v4a_stage_artifact import build_stage_receipt, write_stage_receipt


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src/tech_sentiment").mkdir(parents=True)
    (root / ".github/workflows").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (root / ".github/workflows/fixture.yml").write_text(
        "name: fixture\non:\n  workflow_dispatch:\n", encoding="utf-8"
    )
    (root / "src/tech_sentiment/helper.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (root / "scripts/producer.py").write_text(
        "from tech_sentiment.helper import VALUE\nprint(VALUE)\n",
        encoding="utf-8",
    )
    return root


def _install_fixture_spec(monkeypatch, root: Path) -> None:
    monkeypatch.setitem(
        persistent.STAGE_SPECS,
        "fixture",
        StageSpec(
            family="fixture",
            stage_kind="fixture_kind",
            stage_id="fixture",
            entrypoints=("scripts/producer.py",),
            extra_files=(".github/workflows/fixture.yml",),
        ),
    )


def _stage(tmp_path: Path, *, source_commit: str = "abc") -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "data.txt").write_text("evidence\n", encoding="utf-8")
    receipt = build_stage_receipt(
        root=stage,
        files=[stage / "data.txt"],
        stage_kind="fixture_kind",
        stage_id="fixture",
        source_commit=source_commit,
        start_date="2022-01-04",
        end_date="2026-09-17",
    )
    write_stage_receipt(stage / "receipt.json", receipt)
    return stage


def test_producer_fingerprint_tracks_transitive_local_code_not_unrelated_files(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    _install_fixture_spec(monkeypatch, root)

    first = persistent.producer_fingerprint(root, "fixture")
    (root / "README.md").write_text("unrelated\n", encoding="utf-8")
    second = persistent.producer_fingerprint(root, "fixture")
    assert first["producer_fingerprint"] == second["producer_fingerprint"]

    (root / "src/tech_sentiment/helper.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    third = persistent.producer_fingerprint(root, "fixture")
    assert third["producer_fingerprint"] != first["producer_fingerprint"]
    paths = {row["path"] for row in first["producer_files"]}
    assert "scripts/producer.py" in paths
    assert "src/tech_sentiment/helper.py" in paths


def test_persistent_bundle_reuses_across_source_commits_when_producer_matches(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    _install_fixture_spec(monkeypatch, root)
    stage = _stage(tmp_path, source_commit="old-commit")
    dist = tmp_path / "dist"

    manifest = persistent.package_stage_bundle(
        repo_root=root,
        stage_root=stage,
        family="fixture",
        source_commit="old-commit",
        start_date="2022-01-04",
        end_date="2026-09-17",
        out_dir=dist,
    )
    assert manifest["original_source_commit"] == "old-commit"

    extract = tmp_path / "extract"
    verified = persistent.verify_and_extract_stage_bundle(
        repo_root=root,
        archive_path=dist / f"{manifest['asset_base']}.tar.gz",
        manifest_path=dist / f"{manifest['asset_base']}.manifest.json",
        family="fixture",
        start_date="2022-01-04",
        end_date="2026-09-17",
        extract_to=extract,
    )
    assert verified["bundle_identity"] == manifest["bundle_identity"]
    assert (extract / "data.txt").read_text(encoding="utf-8") == "evidence\n"


def test_persistent_bundle_rejects_changed_producer_or_input_identity(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    _install_fixture_spec(monkeypatch, root)
    stage = _stage(tmp_path, source_commit="old-commit")
    dist = tmp_path / "dist"
    manifest = persistent.package_stage_bundle(
        repo_root=root,
        stage_root=stage,
        family="fixture",
        source_commit="old-commit",
        start_date="2022-01-04",
        end_date="2026-09-17",
        out_dir=dist,
    )

    (root / "src/tech_sentiment/helper.py").write_text(
        "VALUE = 99\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="producer_fingerprint|compatibility_key"):
        persistent.verify_and_extract_stage_bundle(
            repo_root=root,
            archive_path=dist / f"{manifest['asset_base']}.tar.gz",
            manifest_path=dist / f"{manifest['asset_base']}.manifest.json",
            family="fixture",
            start_date="2022-01-04",
            end_date="2026-09-17",
            extract_to=tmp_path / "changed",
        )


def test_persistent_bundle_archive_is_deterministic(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _install_fixture_spec(monkeypatch, root)
    stage = _stage(tmp_path, source_commit="abc")

    one = persistent.package_stage_bundle(
        repo_root=root,
        stage_root=stage,
        family="fixture",
        source_commit="abc",
        start_date="2022-01-04",
        end_date="2026-09-17",
        out_dir=tmp_path / "one",
    )
    two = persistent.package_stage_bundle(
        repo_root=root,
        stage_root=stage,
        family="fixture",
        source_commit="abc",
        start_date="2022-01-04",
        end_date="2026-09-17",
        out_dir=tmp_path / "two",
    )
    assert one["archive_sha256"] == two["archive_sha256"]
    assert one["bundle_identity"] == two["bundle_identity"]


def test_persistent_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("bad", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(payload, arcname="../escape.txt")
    with pytest.raises(ValueError, match="unsafe"):
        persistent._safe_extract(archive, tmp_path / "out")


def test_input_manifest_identity_participates_in_compatibility_key(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path)
    _install_fixture_spec(monkeypatch, root)
    input_manifest = tmp_path / "input.json"
    base = {
        "schema_version": persistent.PERSISTENT_STAGE_SCHEMA,
        "family": "shared",
        "stage_id": "shared",
        "compatibility_key": "shared-key",
        "archive_sha256": "a" * 64,
        "original_source_commit": "source-a",
    }
    base["bundle_identity"] = persistent._manifest_identity(base)
    input_manifest.write_text(json.dumps(base), encoding="utf-8")
    first = persistent.compatibility_descriptor(
        repo_root=root,
        family="fixture",
        start_date="2022-01-04",
        end_date="2026-09-17",
        input_manifests={"shared": input_manifest},
    )
    base["original_source_commit"] = "source-b"
    base["bundle_identity"] = persistent._manifest_identity(base)
    input_manifest.write_text(json.dumps(base), encoding="utf-8")
    second = persistent.compatibility_descriptor(
        repo_root=root,
        family="fixture",
        start_date="2022-01-04",
        end_date="2026-09-17",
        input_manifests={"shared": input_manifest},
    )
    assert first["compatibility_key"] != second["compatibility_key"]


def _write_szse_migration_marker(root: Path, **overrides: object) -> Path:
    marker = root / "shards" / "szse" / persistent._SZSE_MIGRATION_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "v4a-szse-security-code-migration-v1",
        "status": "FROZEN_ENGINEERING_IDENTITY_ALIAS",
        "same_listed_security_identity_only": True,
        "evidence_source_eligibility_changed": False,
        "pit_no_lookahead_semantics_changed": False,
        "research_scope_changed": False,
        "future_outcomes_used": False,
        "production_authority_changed": False,
        "trading_authority_changed": False,
    }
    payload.update(overrides)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def test_szse_bundle_contract_requires_frozen_same_security_marker(tmp_path):
    root = tmp_path / "issuer"
    root.mkdir()
    with pytest.raises(ValueError, match="missing frozen"):
        persistent._validate_family_stage_contract(root, "issuer_szse")

    _write_szse_migration_marker(root)
    persistent._validate_family_stage_contract(root, "issuer_szse")


def test_szse_bundle_contract_rejects_boundary_drift(tmp_path):
    root = tmp_path / "issuer"
    root.mkdir()
    _write_szse_migration_marker(
        root,
        evidence_source_eligibility_changed=True,
    )
    with pytest.raises(ValueError, match="boundary drift"):
        persistent._validate_family_stage_contract(root, "issuer_szse")
