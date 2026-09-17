from __future__ import annotations

import json
from pathlib import Path

import pytest

from tech_sentiment.materialization_manifest import (
    build_materialization_manifest,
    file_sha256,
    write_manifest,
)


def test_manifest_is_deterministic_and_hashes_files(tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("date,value\n2026-09-17,1\n", encoding="utf-8")
    b.write_text("date,value\n2026-09-17,2\n", encoding="utf-8")

    kwargs = dict(
        dataset_id="SSE_SZSE_A_SHARES_TURNOVER",
        schema_version="v1",
        readiness_state="QUALIFIED_INPUT",
        target_start="2022-01-04",
        target_end="2026-09-17",
        source_identities=["SZSE", "SSE"],
        provider_interfaces=["provider-b", "provider-a"],
        files=[b, a],
        root=tmp_path,
        metadata={"unit": "CNY"},
    )
    first = build_materialization_manifest(**kwargs)
    second = build_materialization_manifest(**kwargs)
    assert first == second
    assert [row["path"] for row in first["files"]] == ["a.csv", "b.csv"]
    assert first["files"][0]["sha256"] == file_sha256(a)
    assert len(first["manifest_sha256"]) == 64

    out = tmp_path / "manifest.json"
    write_manifest(out, first)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["manifest_sha256"] == first["manifest_sha256"]


def test_qualified_input_requires_source_and_provider(tmp_path: Path) -> None:
    data = tmp_path / "x.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source identity and provider"):
        build_materialization_manifest(
            dataset_id="x",
            schema_version="v1",
            readiness_state="QUALIFIED_INPUT",
            target_start="2022-01-04",
            target_end="2026-09-17",
            source_identities=[],
            provider_interfaces=[],
            files=[data],
        )


def test_unknown_readiness_state_fails_closed(tmp_path: Path) -> None:
    data = tmp_path / "x.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid readiness_state"):
        build_materialization_manifest(
            dataset_id="x",
            schema_version="v1",
            readiness_state="MAYBE",
            target_start="2022-01-04",
            target_end="2026-09-17",
            source_identities=["SSE"],
            provider_interfaces=["provider"],
            files=[data],
        )
