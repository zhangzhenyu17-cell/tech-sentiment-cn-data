from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from tech_sentiment.immutable_checkpoint import (
    CheckpointIdentity,
    ImmutableCheckpointStore,
)
from tech_sentiment.prospective_context_checkpoint_v1 import (
    CHECKPOINT_BUNDLE_SCHEMA,
    checkpoint_release_tag,
    package_complete_checkpoints,
    plan_available_checkpoint_bundles,
    restore_checkpoint_bundle,
    semantic_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def _identity(name: str) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer=name,
        producer_version="test-v1",
        source_commit="semantic:test",
        source_identities=("PUBLIC_TEST_SOURCE",),
        query_identity={"capture_date": "2026-09-21"},
        scope={
            "start_date": "2026-09-01",
            "end_date": "2026-09-21",
            "trading_dates": ["2026-09-21"],
        },
    )


def _save(
    root: Path,
    *,
    store_name: str,
    identity: CheckpointIdentity,
    permanent: bool,
) -> None:
    store = ImmutableCheckpointStore(root / store_name)
    store.save(
        identity,
        frames={
            "data": pd.DataFrame(
                {
                    "date": ["2026-09-21"],
                    "value": [1.0],
                }
            ),
            "errors": pd.DataFrame(columns=["date", "error"]),
        },
        metadata={
            "capture_date": "2026-09-21",
            "actual_source_commit": "deadbeef",
            "permanent_reuse_eligible": permanent,
        },
    )


def test_semantic_fingerprint_covers_contract_and_transport_for_both_universes() -> None:
    for universe in ("STAR50", "ChiNext50"):
        payload = semantic_fingerprint(ROOT, family=f"universe:{universe}")
        files = {row["path"] for row in payload["producer_files"]}
        assert "reference/prospective_context_raw_v1.json" in files
        assert "src/tech_sentiment/bounded_retry.py" in files
        assert payload["checkpoint_revision"].startswith("semantic:")


def test_progress_snapshot_packages_only_permanent_units_and_restores_exactly(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    eligible = _identity("eligible")
    skipped = _identity("skipped")
    _save(cache, store_name="sse", identity=eligible, permanent=True)
    _save(cache, store_name="szse", identity=skipped, permanent=False)

    first = tmp_path / "first"
    second = tmp_path / "second"
    one = package_complete_checkpoints(
        cache,
        out_dir=first,
        operation_date="2026-09-21",
    )
    two = package_complete_checkpoints(
        cache,
        out_dir=second,
        operation_date="2026-09-21",
    )

    assert one["release_tag"] == "prospective-context-checkpoints-2026-09-21"
    assert len(one["bundles"]) == 1
    manifest = one["bundles"][0]
    assert manifest["schema_version"] == CHECKPOINT_BUNDLE_SCHEMA
    assert len(manifest["checkpoint_units"]) == 1
    assert manifest["checkpoint_units"][0]["checkpoint_fingerprint"] == eligible.fingerprint
    assert manifest["formal_evidence_handoff"] is False
    assert manifest["qualification_granted"] is False
    assert one["bundles"][0]["bundle_identity"] == two["bundles"][0]["bundle_identity"]

    base = manifest["asset_base"]
    assert (first / f"{base}.tar.gz").read_bytes() == (
        second / f"{base}.tar.gz"
    ).read_bytes()

    restored_root = tmp_path / "restored"
    restored = restore_checkpoint_bundle(
        archive_path=first / f"{base}.tar.gz",
        manifest_path=first / f"{base}.manifest.json",
        cache_root=restored_root,
    )
    assert restored["bundle_identity"] == manifest["bundle_identity"]
    loaded = ImmutableCheckpointStore(restored_root / "sse").load(eligible)
    assert loaded is not None
    assert loaded.frames["data"].loc[0, "value"] == 1.0
    assert ImmutableCheckpointStore(restored_root / "szse").load(skipped) is None


def test_restore_plan_selects_only_snapshots_that_add_expected_work(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    one = _identity("one")
    _save(cache, store_name="sse", identity=one, permanent=True)
    out = tmp_path / "out"
    packaged = package_complete_checkpoints(
        cache,
        out_dir=out,
        operation_date="2026-09-21",
    )
    manifest = packaged["bundles"][0]

    expected = {
        "operation_date": "2026-09-21",
        "assets": [
            {"fingerprint": one.fingerprint},
            {"fingerprint": "f" * 64},
        ],
    }
    plan = plan_available_checkpoint_bundles(
        expected=expected,
        manifests=[manifest],
    )
    assert plan["selected_count"] == 1
    assert plan["covered_checkpoint_count"] == 1
    assert plan["missing_checkpoint_count"] == 1


def test_progress_manifest_rejects_cross_date_restore_plan(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    identity = _identity("one")
    _save(cache, store_name="sse", identity=identity, permanent=True)
    packaged = package_complete_checkpoints(
        cache,
        out_dir=tmp_path / "out",
        operation_date="2026-09-21",
    )
    plan = plan_available_checkpoint_bundles(
        expected={
            "operation_date": "2026-09-22",
            "assets": [{"fingerprint": identity.fingerprint}],
        },
        manifests=packaged["bundles"],
    )
    assert plan["selected_count"] == 0
    assert plan["missing_checkpoint_count"] == 1
    assert checkpoint_release_tag("2026-09-22") != packaged["release_tag"]


def test_checkpoint_cli_package_and_restore_entrypoints(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    identity = _identity("cli")
    _save(cache, store_name="sse", identity=identity, permanent=True)
    out = tmp_path / "bundle"
    package_json = tmp_path / "package.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/prospective_context_checkpoint_bundle.py",
            "package",
            "--cache-root",
            str(cache),
            "--out-dir",
            str(out),
            "--operation-date",
            "2026-09-21",
            "--out",
            str(package_json),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    base = payload["bundles"][0]["asset_base"]

    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in (".tar.gz", ".manifest.json", ".sha256"):
        (assets / f"{base}{suffix}").write_bytes(
            (out / f"{base}{suffix}").read_bytes()
        )

    restored = tmp_path / "restored"
    restore_json = tmp_path / "restore.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prospective_context_checkpoint_bundle.py",
            "restore",
            "--asset-dir",
            str(assets),
            "--cache-root",
            str(restored),
            "--out",
            str(restore_json),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    restored_payload = json.loads(restore_json.read_text(encoding="utf-8"))
    assert restored_payload["restored_count"] == 1
    assert ImmutableCheckpointStore(restored / "sse").load(identity) is not None


def test_checkpoint_publisher_never_clobbers_existing_assets() -> None:
    text = (
        ROOT / "scripts/publish_prospective_context_checkpoint_bundle.sh"
    ).read_text(encoding="utf-8")
    assert "--clobber" not in text
    assert "immutable checkpoint asset exists with different bytes" in text
    assert 'gh release upload "$TAG" "${missing_assets[@]}"' in text
