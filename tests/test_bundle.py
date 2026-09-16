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
                "date,symbol,close\n2026-09-16,000001,10\n2026-09-16,000002,20\n",
                encoding="utf-8",
            )
        elif name == "universe_point_in_time.csv":
            path.write_text(
                "symbol,effective_start,effective_end\n"
                "000001,2026-01-01,\n000002,2026-01-01,\n",
                encoding="utf-8",
            )
        elif name == "index_prices.csv":
            path.write_text("date,index_code,close\n2026-09-16,000688,1000\n", encoding="utf-8")
        else:
            path.write_text("value\n1\n", encoding="utf-8")
    return source


def test_bundle_contains_only_allowlisted_public_data(tmp_path: Path) -> None:
    source = _fixture_dir(tmp_path)
    (source / "private_signal.json").write_text('{"temperature": 20}', encoding="utf-8")
    output = tmp_path / "dist"

    manifest = build_market_bundle(source, output)

    assert manifest["bundle_kind"] == "public_market_data"
    assert manifest["contains_model_output"] is False
    assert manifest["market_date"] == "2026-09-16"
    assert manifest["quality"]["active_latest_day_coverage"] == 1.0
    assert {item["path"] for item in manifest["files"]} == set(REQUIRED_FILES)
    with tarfile.open(output / "market_bundle_latest.tar.gz", "r:gz") as archive:
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
