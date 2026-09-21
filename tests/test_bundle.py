from __future__ import annotations

import json
from pathlib import Path
import tarfile

import pytest

from tech_sentiment.bundle import REQUIRED_FILES, build_market_bundle


def _fixture_dir(tmp_path: Path) -> Path:
    source = tmp_path / "daily"
    source.mkdir()
    for name in REQUIRED_FILES:
        path = source / name
        if name == "production_universe_meta.json":
            path.write_text(
                json.dumps({"target_date": "2026-09-16", "universe_mode": "point_in_time"}),
                encoding="utf-8",
            )
        elif name == "prices.csv":
            path.write_text(
                "date,symbol,close,provider\n"
                "2026-09-16,000001,10,tencent\n"
                "2026-09-16,000002,20,eastmoney\n",
                encoding="utf-8",
            )
        elif name == "universe_point_in_time.csv":
            path.write_text(
                "symbol,effective_start,effective_end\n"
                "000001,2026-01-01,\n000002,2026-01-01,\n",
                encoding="utf-8",
            )
        elif name == "index_prices.csv":
            path.write_text(
                "date,index_code,close,provider\n2026-09-16,000688,1000,csindex\n",
                encoding="utf-8",
            )
        else:
            path.write_text("value\n1\n", encoding="utf-8")
    return source


def test_bundle_contains_only_allowlisted_public_data(tmp_path: Path) -> None:
    source = _fixture_dir(tmp_path)
    (source / "private_signal.json").write_text('{"temperature": 20}', encoding="utf-8")
    output = tmp_path / "dist"

    manifest = build_market_bundle(source, output, public_repo_git_sha="a" * 40)

    assert manifest["bundle_kind"] == "public_market_data"
    assert manifest["contains_model_output"] is False
    assert manifest["market_date"] == "2026-09-16"
    assert manifest["public_repo_git_sha"] == "a" * 40
    assert manifest["source_providers"] == ["csindex", "eastmoney", "tencent"]
    assert manifest["quality"]["active_latest_day_coverage"] == 1.0
    assert {item["path"] for item in manifest["files"]} == set(REQUIRED_FILES)
    with tarfile.open(output / "market_bundle_latest.tar.gz", "r:gz") as archive:
        names = set(archive.getnames())
    assert "market_bundle/private_signal.json" not in names
    assert "market_bundle/manifest.json" in names


def test_dated_bundle_is_deterministic_and_contains_durable_identity(tmp_path: Path) -> None:
    source = _fixture_dir(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_market_bundle(source, first, public_repo_git_sha="b" * 40)
    build_market_bundle(source, second, public_repo_git_sha="b" * 40)

    name = "market-bundle-2026-09-16"
    assert (first / f"{name}.tar.gz").read_bytes() == (second / f"{name}.tar.gz").read_bytes()
    assert (first / f"{name}.sha256").read_bytes() == (second / f"{name}.sha256").read_bytes()
    payload = json.loads((first / f"{name}.manifest.json").read_text(encoding="utf-8"))
    assert payload["bundle_kind"] == "public_market_data_dated_immutable"
    assert payload["market_date"] == "2026-09-16"
    assert payload["target_date"] == "2026-09-16"
    assert payload["public_repo_git_sha"] == "b" * 40
    assert payload["contains_model_output"] is False
    assert payload["contains_private_evidence"] is False
    assert "generated_at_utc" not in payload

    with tarfile.open(first / f"{name}.tar.gz", "r:gz") as archive:
        names = set(archive.getnames())
    assert "market_bundle/private_signal.json" not in names
    assert "market_bundle/manifest.json" in names


def test_bundle_fails_closed_when_required_file_is_missing(tmp_path: Path) -> None:
    source = _fixture_dir(tmp_path)
    (source / "prices.csv").unlink()
    with pytest.raises(FileNotFoundError, match="prices.csv"):
        build_market_bundle(source, tmp_path / "dist")


def test_bundle_rejects_non_point_in_time_universe(tmp_path: Path) -> None:
    source = _fixture_dir(tmp_path)
    (source / "production_universe_meta.json").write_text(
        json.dumps({"target_date": "2026-09-16", "universe_mode": "current_snapshot"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="point-in-time"):
        build_market_bundle(source, tmp_path / "dist")
