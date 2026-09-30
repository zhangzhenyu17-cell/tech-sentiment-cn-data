from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .cnindex_index_price import fetch_cnindex_history
from .csindex_index_price import fetch_csindex_history


SCHEMA_VERSION = "cross-sector-relative-mispricing-v0-public-input-v1"
PRODUCT_ID = "CROSS_SECTOR_RELATIVE_MISPRICING_V0_PUBLIC_INPUT"
MINIMUM_HISTORY_SESSIONS = 451

BENCHMARK_SPECS = (
    {
        "domain_id": "TECHNOLOGY",
        "benchmark_id": "TECHNOLOGY_STAR50",
        "benchmark_role": "DOMAIN_COMPONENT",
        "index_code": "000688",
        "index_name": "STAR50",
        "source_system": "CSI_OFFICIAL_INDEX_PERF",
    },
    {
        "domain_id": "TECHNOLOGY",
        "benchmark_id": "TECHNOLOGY_CHINEXT50",
        "benchmark_role": "DOMAIN_COMPONENT",
        "index_code": "399673",
        "index_name": "CHINEXT50",
        "source_system": "CNINDEX_OFFICIAL_MARKET_DAILY",
    },
    {
        "domain_id": "INNOVATION_DRUG",
        "benchmark_id": "INNOVATION_DRUG_931152",
        "benchmark_role": "DOMAIN_PRIMARY",
        "index_code": "931152",
        "index_name": "CSI_INNOVATIVE_DRUG_INDUSTRY",
        "source_system": "CSI_OFFICIAL_INDEX_PERF",
    },
    {
        "domain_id": "DEFENSE",
        "benchmark_id": "DEFENSE_399973",
        "benchmark_role": "DOMAIN_PRIMARY",
        "index_code": "399973",
        "index_name": "CSI_DEFENSE",
        "source_system": "CSI_OFFICIAL_INDEX_PERF",
    },
    {
        "domain_id": "CORE_BETA",
        "benchmark_id": "CORE_BETA_A500",
        "benchmark_role": "DOMAIN_PRIMARY",
        "index_code": "000510",
        "index_name": "CSI_A500",
        "source_system": "CSI_OFFICIAL_INDEX_PERF",
    },
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_number(value: object) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def build_cross_sector_public_input(
    *,
    start_date: str,
    as_of_date: str,
    source_commit: str,
    output_csv: Path,
    output_manifest: Path,
    fetcher: Callable[..., pd.DataFrame] | None = None,
    csindex_fetcher: Callable[..., pd.DataFrame] = fetch_csindex_history,
    cnindex_fetcher: Callable[..., pd.DataFrame] = fetch_cnindex_history,
) -> dict[str, Any]:
    if not source_commit or len(str(source_commit)) < 7:
        raise ValueError("source_commit is required")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(as_of_date).normalize()
    if start > end:
        raise ValueError("start_date exceeds as_of_date")

    frames: list[pd.DataFrame] = []
    latest_dates: dict[str, str] = {}
    rows_by_benchmark: dict[str, int] = {}
    pe_rows_by_benchmark: dict[str, int] = {}
    latest_pe_by_benchmark: dict[str, float | None] = {}
    valuation_state_by_benchmark: dict[str, str] = {}
    source_identity_by_benchmark: dict[str, str] = {}
    providers: set[str] = set()

    for spec in BENCHMARK_SPECS:
        code = str(spec["index_code"])
        active_fetcher = fetcher
        if active_fetcher is None:
            active_fetcher = (
                cnindex_fetcher
                if spec["source_system"] == "CNINDEX_OFFICIAL_MARKET_DAILY"
                else csindex_fetcher
            )
        frame = active_fetcher(
            code,
            start_date=start_date,
            end_date=as_of_date,
            retries=2,
            retry_backoff_seconds=0.5,
            timeout_seconds=20.0,
        ).copy()

        required = {
            "date",
            "index_code",
            "close",
            "rolling_pe",
            "provider",
            "provider_identifier",
        }
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{code} official input missing columns: {sorted(missing)}")
        if frame.empty:
            raise ValueError(f"{code} official input is empty")

        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
        frame["index_code"] = frame["index_code"].astype(str).str.zfill(6)
        if set(frame["index_code"]) != {code}:
            raise ValueError(f"{code} index identity drift")
        if frame["date"].duplicated().any():
            raise ValueError(f"{code} duplicate market dates")
        if (frame["date"] > end).any():
            raise ValueError(f"{code} contains future rows")
        if len(frame) < MINIMUM_HISTORY_SESSIONS:
            raise ValueError(
                f"{code} has insufficient history: {len(frame)} < {MINIMUM_HISTORY_SESSIONS}"
            )

        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        if frame["close"].isna().any() or (frame["close"] <= 0).any():
            raise ValueError(f"{code} contains invalid close values")
        frame["rolling_pe"] = pd.to_numeric(frame["rolling_pe"], errors="coerce")
        if "valuation_source_state" not in frame.columns:
            frame["valuation_source_state"] = (
                "OFFICIAL_HISTORICAL_VALUATION_AVAILABLE"
                if (frame["rolling_pe"] > 0).any()
                else "OFFICIAL_HISTORICAL_VALUATION_UNAVAILABLE"
            )
        if "sample_count" in frame.columns:
            frame["sample_count"] = pd.to_numeric(frame["sample_count"], errors="coerce")

        frame.insert(1, "domain_id", str(spec["domain_id"]))
        frame.insert(2, "benchmark_id", str(spec["benchmark_id"]))
        frame.insert(3, "benchmark_role", str(spec["benchmark_role"]))
        frame.insert(4, "index_name", str(spec["index_name"]))
        frame["point_in_time"] = True
        frame["source_observation_date"] = frame["date"].dt.strftime("%Y-%m-%d")
        frame["availability_semantics"] = (
            "OFFICIAL_DAILY_OBSERVATION_DATE_NO_INTRADAY_TIMESTAMP_CLAIM"
        )

        benchmark_id = str(spec["benchmark_id"])
        latest_row = frame.sort_values("date").iloc[-1]
        latest_dates[benchmark_id] = latest_row["date"].date().isoformat()
        rows_by_benchmark[benchmark_id] = int(len(frame))
        pe_rows_by_benchmark[benchmark_id] = int((frame["rolling_pe"] > 0).sum())
        latest_pe_by_benchmark[benchmark_id] = _json_number(latest_row["rolling_pe"])
        valuation_state_by_benchmark[benchmark_id] = str(
            latest_row["valuation_source_state"]
        )
        source_identity_by_benchmark[benchmark_id] = str(spec["source_system"])
        providers.update(str(x) for x in frame["provider"].dropna().unique())
        frames.append(frame)

    latest_market_date = min(latest_dates.values())
    common_date = pd.Timestamp(latest_market_date)
    aligned_frames = [
        frame.loc[frame["date"] <= common_date].copy()
        for frame in frames
    ]
    if any(frame.empty for frame in aligned_frames):
        raise ValueError("latest common official market date produced an empty benchmark rail")

    output = pd.concat(aligned_frames, ignore_index=True)
    output = output.sort_values(["benchmark_id", "date"]).reset_index(drop=True)
    if output.groupby("benchmark_id")["date"].max().nunique() != 1:
        raise ValueError("common-date alignment failed")

    rows_by_benchmark = {
        benchmark_id: int(len(group))
        for benchmark_id, group in output.groupby("benchmark_id", sort=False)
    }
    pe_rows_by_benchmark = {
        benchmark_id: int((group["rolling_pe"] > 0).sum())
        for benchmark_id, group in output.groupby("benchmark_id", sort=False)
    }
    latest_pe_by_benchmark = {}
    valuation_state_by_benchmark = {}
    for benchmark_id, group in output.groupby("benchmark_id", sort=False):
        latest_row = group.sort_values("date").iloc[-1]
        latest_pe_by_benchmark[benchmark_id] = _json_number(latest_row["rolling_pe"])
        valuation_state_by_benchmark[benchmark_id] = str(
            latest_row["valuation_source_state"]
        )

    if any(count < MINIMUM_HISTORY_SESSIONS for count in rows_by_benchmark.values()):
        raise ValueError(
            f"common-date alignment violates history floor: {rows_by_benchmark}"
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_csv, index=False, date_format="%Y-%m-%d")

    valuation_complete = all(
        state == "OFFICIAL_HISTORICAL_VALUATION_AVAILABLE"
        for state in valuation_state_by_benchmark.values()
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product_id": PRODUCT_ID,
        "status": (
            "PUBLIC_RAW_PIT_PRICE_VALUATION_INPUT_READY_NO_PRIVATE_QUALIFICATION"
            if valuation_complete
            else "PUBLIC_RAW_PIT_COMMON_DATE_PRICE_INPUT_READY_VALUATION_PARTIAL_NO_PRIVATE_QUALIFICATION"
        ),
        "requested_as_of_date": as_of_date,
        "latest_market_date": latest_market_date,
        "latest_available_market_date_by_benchmark": latest_dates,
        "alignment_policy": "LATEST_COMMON_OFFICIAL_MARKET_DATE_NO_FORWARD_FILL",
        "lagging_benchmarks_vs_requested_as_of": {
            benchmark_id: market_date
            for benchmark_id, market_date in latest_dates.items()
            if market_date < as_of_date
        },
        "start_date": start_date,
        "minimum_history_sessions": MINIMUM_HISTORY_SESSIONS,
        "source_repository": "zhangzhenyu17-cell/tech-sentiment-cn-data",
        "source_commit": str(source_commit),
        "source_identity": "MULTI_OFFICIAL_INDEX_SOURCES",
        "source_identities_by_benchmark": source_identity_by_benchmark,
        "benchmarks": [dict(spec) for spec in BENCHMARK_SPECS],
        "row_count": int(len(output)),
        "rows_by_benchmark": rows_by_benchmark,
        "positive_rolling_pe_rows_by_benchmark": pe_rows_by_benchmark,
        "latest_rolling_pe_by_benchmark": latest_pe_by_benchmark,
        "valuation_source_state_by_benchmark": valuation_state_by_benchmark,
        "providers": sorted(providers),
        "csv_path": output_csv.as_posix(),
        "csv_sha256": _sha256(output_csv),
        "contains_model_output": False,
        "contains_private_model_semantics": False,
        "contains_forward_outcomes": False,
        "contains_portfolio_or_holdings_data": False,
        "historical_rows_are_public_observations_not_outcome_labels": True,
        "public_handoff_ready_grants_private_qualification": False,
        "automatic_trigger": False,
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "BENCHMARK_SPECS",
    "MINIMUM_HISTORY_SESSIONS",
    "PRODUCT_ID",
    "SCHEMA_VERSION",
    "build_cross_sector_public_input",
]
