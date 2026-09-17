from __future__ import annotations

from pathlib import Path

from tech_sentiment.materialization_manifest import (
    build_materialization_manifest,
    build_readiness_matrix,
    file_sha256,
)


def test_manifest_is_deterministic_and_hashes_materialized_files(tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    b = tmp_path / "b.json"
    a.write_text("date,value\n2026-09-17,1\n", encoding="utf-8")
    b.write_text('{"ok": true}\n', encoding="utf-8")
    readiness = {
        "588000_long_flow": "PARTIAL_COVERAGE",
        "sse_szse_a_shares_turnover": "QUALIFIED_INPUT",
    }
    kwargs = dict(
        schema_version="v1",
        target_start="2022-01-04",
        target_end="2026-09-17",
        root=tmp_path,
        files=[b, a],
        readiness_matrix=readiness,
        source_identities=["SZSE", "SSE"],
        query_identities={"q": "abc"},
    )
    first = build_materialization_manifest(**kwargs)
    second = build_materialization_manifest(**kwargs)
    assert first == second
    assert [row["path"] for row in first["files"]] == ["a.csv", "b.json"]
    assert first["files"][0]["sha256"] == file_sha256(a)
    assert len(first["manifest_sha256"]) == 64
    assert first["production_or_model_output"] is False
    assert first["ready_manual_only"] is False
    assert first["schedule_allowed"] is False


def test_readiness_requires_complete_financing_and_does_not_overqualify_cninfo() -> None:
    capital = {
        "etf_readiness_state": "PARTIAL_COVERAGE",
        "turnover_readiness_state": "QUALIFIED_INPUT",
    }
    financing = {
        "qualification_state": "CANONICAL_UNIT_QUALIFIED",
        "bilateral_coverage": 0.99,
    }
    pit = {
        "failed_symbol_queries": 0,
        "materialized_records": 100,
    }
    matrix = build_readiness_matrix(
        capital_summary=capital,
        financing_summary=financing,
        pit_summary=pit,
    )
    assert matrix["588000_long_flow"] == "PARTIAL_COVERAGE"
    assert matrix["sse_szse_a_shares_turnover"] == "QUALIFIED_INPUT"
    assert matrix["financing"] == "PARTIAL_COVERAGE"
    assert matrix["fundamental_pit"] == "PARTIAL_COVERAGE"
    assert matrix["earnings_pit"] == "PARTIAL_COVERAGE"
    assert matrix["valuation_pit"] == "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    assert matrix["major_event_pit"] == "PARTIAL_COVERAGE"
    assert matrix["major_negative_exclusion"] == "DATA_INSUFFICIENT"
    assert matrix["clean_forward_external_evidence"] == "DATA_INSUFFICIENT"


def test_missing_materializers_remain_unmaterialized() -> None:
    matrix = build_readiness_matrix(
        capital_summary={
            "etf_readiness_state": "DATA_INSUFFICIENT",
            "turnover_readiness_state": "PARTIAL_COVERAGE",
        },
        financing_summary=None,
        pit_summary=None,
    )
    assert matrix["financing"] == "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    assert matrix["fundamental_pit"] == "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    assert matrix["earnings_pit"] == "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    assert matrix["major_event_pit"] == "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
