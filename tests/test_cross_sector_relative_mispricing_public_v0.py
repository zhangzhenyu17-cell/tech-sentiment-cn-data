from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.cross_sector_relative_mispricing_public_v0 import (
    BENCHMARK_SPECS,
    MINIMUM_HISTORY_SESSIONS,
    PRODUCT_ID,
    SCHEMA_VERSION,
    build_cross_sector_public_input,
)


def _fake_fetcher(
    code: str,
    *,
    start_date: str,
    end_date: str,
    retries: int,
    retry_backoff_seconds: float,
    timeout_seconds: float,
) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-09-30", periods=480)
    base = 1000.0 + int(code[-2:])
    frame = pd.DataFrame(
        {
            "date": dates,
            "index_code": code,
            "open": [base + i for i in range(len(dates))],
            "high": [base + i + 2 for i in range(len(dates))],
            "low": [base + i - 2 for i in range(len(dates))],
            "close": [base + i + 1 for i in range(len(dates))],
            "pct_chg": 0.1,
            "volume": 100,
            "amount": 1000,
            "sample_count": 50,
            "rolling_pe": [20.0 + i / 100 for i in range(len(dates))],
            "provider": "csindex:index_perf",
            "provider_identifier": code,
        }
    )
    return frame[frame["date"] <= pd.Timestamp(end_date)].copy()


def test_public_builder_materializes_exact_five_benchmark_price_and_pe_rails(
    tmp_path: Path,
) -> None:
    out = tmp_path / "public.csv"
    manifest_path = tmp_path / "manifest.json"
    manifest = build_cross_sector_public_input(
        start_date="2024-01-01",
        as_of_date="2026-09-30",
        source_commit="a" * 40,
        output_csv=out,
        output_manifest=manifest_path,
        fetcher=_fake_fetcher,
    )

    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["product_id"] == PRODUCT_ID
    assert manifest["row_count"] == 480 * len(BENCHMARK_SPECS)
    assert set(manifest["rows_by_benchmark"]) == {
        spec["benchmark_id"] for spec in BENCHMARK_SPECS
    }
    assert all(
        n == 480 for n in manifest["positive_rolling_pe_rows_by_benchmark"].values()
    )
    assert manifest["contains_forward_outcomes"] is False
    assert manifest["public_handoff_ready_grants_private_qualification"] is False

    frame = pd.read_csv(out, dtype={"index_code": str})
    assert set(frame["domain_id"]) == {
        "TECHNOLOGY",
        "INNOVATION_DRUG",
        "DEFENSE",
        "CORE_BETA",
    }
    assert set(frame["index_code"].str.zfill(6)) == {
        "000688",
        "399673",
        "931152",
        "399973",
        "000510",
    }
    assert frame["point_in_time"].astype(bool).all()
    assert frame["rolling_pe"].notna().all()

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["csv_sha256"] == manifest["csv_sha256"]


def test_public_builder_fails_closed_when_official_pe_column_is_missing(
    tmp_path: Path,
) -> None:
    def missing_pe(code: str, **kwargs) -> pd.DataFrame:
        return _fake_fetcher(code, **kwargs).drop(columns=["rolling_pe"])

    with pytest.raises(ValueError, match="rolling_pe"):
        build_cross_sector_public_input(
            start_date="2024-01-01",
            as_of_date="2026-09-30",
            source_commit="b" * 40,
            output_csv=tmp_path / "public.csv",
            output_manifest=tmp_path / "manifest.json",
            fetcher=missing_pe,
        )


def test_public_builder_requires_history_floor_and_aligned_latest_date(
    tmp_path: Path,
) -> None:
    assert MINIMUM_HISTORY_SESSIONS == 451

    def short(code: str, **kwargs) -> pd.DataFrame:
        return _fake_fetcher(code, **kwargs).iloc[:200].copy()

    with pytest.raises(ValueError, match="insufficient history"):
        build_cross_sector_public_input(
            start_date="2024-01-01",
            as_of_date="2026-09-30",
            source_commit="c" * 40,
            output_csv=tmp_path / "public.csv",
            output_manifest=tmp_path / "manifest.json",
            fetcher=short,
        )


def test_default_source_routing_keeps_chinext50_price_but_fails_closed_on_historical_pe(
    tmp_path: Path,
) -> None:
    calls = {"csi": [], "cni": []}

    def csi_fetcher(code: str, **kwargs) -> pd.DataFrame:
        calls["csi"].append(code)
        return _fake_fetcher(code, **kwargs)

    def cni_fetcher(code: str, **kwargs) -> pd.DataFrame:
        calls["cni"].append(code)
        frame = _fake_fetcher(code, **kwargs)
        frame["rolling_pe"] = pd.NA
        frame["sample_count"] = pd.NA
        frame["provider"] = "cnindex:official_market_daily"
        frame["provider_identifier"] = code
        frame["valuation_source_state"] = "OFFICIAL_HISTORICAL_VALUATION_UNAVAILABLE"
        return frame

    manifest = build_cross_sector_public_input(
        start_date="2024-01-01",
        as_of_date="2026-09-30",
        source_commit="d" * 40,
        output_csv=tmp_path / "public.csv",
        output_manifest=tmp_path / "manifest.json",
        csindex_fetcher=csi_fetcher,
        cnindex_fetcher=cni_fetcher,
    )

    assert calls["cni"] == ["399673"]
    assert set(calls["csi"]) == {"000688", "931152", "399973", "000510"}
    assert manifest["status"] == (
        "PUBLIC_RAW_PIT_PRICE_INPUT_READY_VALUATION_PARTIAL_NO_PRIVATE_QUALIFICATION"
    )
    assert manifest["positive_rolling_pe_rows_by_benchmark"]["TECHNOLOGY_CHINEXT50"] == 0
    assert (
        manifest["valuation_source_state_by_benchmark"]["TECHNOLOGY_CHINEXT50"]
        == "OFFICIAL_HISTORICAL_VALUATION_UNAVAILABLE"
    )
    assert (
        manifest["source_identities_by_benchmark"]["TECHNOLOGY_CHINEXT50"]
        == "CNINDEX_OFFICIAL_MARKET_DAILY"
    )
