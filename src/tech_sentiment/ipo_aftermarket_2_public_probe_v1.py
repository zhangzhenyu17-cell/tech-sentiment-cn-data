from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd


INDUSTRY_CHANGE_PROVIDER = "CNINFO_VIA_AKSHARE"
INDUSTRY_PE_PROVIDER = "CNINFO_VIA_AKSHARE"
CSRC_TAXONOMY_LABEL = "证监会行业分类"


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_sleep_seconds: float = 1.0,
) -> Any:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # source probe intentionally records transport failure
            error = exc
            if attempt + 1 < attempts:
                time.sleep(base_sleep_seconds * (2**attempt))
    assert error is not None
    raise error


def normalize_industry_change_probe(
    raw: pd.DataFrame,
    *,
    symbol: str,
) -> dict[str, Any]:
    if raw is None or raw.empty:
        return {
            "symbol": str(symbol).zfill(6),
            "row_count": 0,
            "csrc_row_count": 0,
            "min_change_date": None,
            "max_change_date": None,
            "classification_standards": [],
            "classification_standard_codes": [],
            "industry_codes": [],
            "required_columns_present": False,
        }

    standard_col = _first_column(raw, ("分类标准",))
    standard_code_col = _first_column(raw, ("分类标准编码",))
    industry_code_col = _first_column(raw, ("行业编码",))
    change_date_col = _first_column(raw, ("变更日期",))
    required = all(
        col is not None
        for col in (standard_col, standard_code_col, industry_code_col, change_date_col)
    )
    if not required:
        return {
            "symbol": str(symbol).zfill(6),
            "row_count": int(len(raw)),
            "csrc_row_count": 0,
            "min_change_date": None,
            "max_change_date": None,
            "classification_standards": sorted(
                {str(value) for value in raw[standard_col].dropna()}
            )
            if standard_col
            else [],
            "classification_standard_codes": [],
            "industry_codes": [],
            "required_columns_present": False,
        }

    dates = pd.to_datetime(raw[change_date_col], errors="coerce").dt.normalize()
    standards = raw[standard_col].astype(str)
    # Do not hard-code a provider-specific numeric taxonomy code before the probe.
    # The public source itself must prove which rows are labelled as CSRC.
    csrc_mask = standards.str.contains("证监会", na=False)
    csrc = raw.loc[csrc_mask].copy()

    return {
        "symbol": str(symbol).zfill(6),
        "row_count": int(len(raw)),
        "csrc_row_count": int(csrc_mask.sum()),
        "min_change_date": (
            str(dates.min().date()) if dates.notna().any() else None
        ),
        "max_change_date": (
            str(dates.max().date()) if dates.notna().any() else None
        ),
        "classification_standards": sorted(
            {str(value) for value in raw[standard_col].dropna()}
        ),
        "classification_standard_codes": sorted(
            {str(value) for value in raw[standard_code_col].dropna()}
        ),
        "industry_codes": sorted(
            {str(value) for value in csrc[industry_code_col].dropna()}
        ),
        "required_columns_present": True,
    }


def normalize_industry_pe_probe(
    raw: pd.DataFrame,
    *,
    requested_date: pd.Timestamp,
) -> dict[str, Any]:
    requested = pd.Timestamp(requested_date).normalize()
    if raw is None or raw.empty:
        return {
            "requested_date": str(requested.date()),
            "row_count": 0,
            "exact_date_row_count": 0,
            "positive_static_median_count": 0,
            "industry_code_count": 0,
            "required_columns_present": False,
            "returned_dates": [],
        }

    date_col = _first_column(raw, ("变动日期",))
    industry_code_col = _first_column(raw, ("行业编码",))
    level_col = _first_column(raw, ("行业层级",))
    median_col = _first_column(raw, ("静态市盈率-中位数",))
    required = all(
        col is not None for col in (date_col, industry_code_col, level_col, median_col)
    )
    if not required:
        return {
            "requested_date": str(requested.date()),
            "row_count": int(len(raw)),
            "exact_date_row_count": 0,
            "positive_static_median_count": 0,
            "industry_code_count": 0,
            "required_columns_present": False,
            "returned_dates": [],
        }

    dates = pd.to_datetime(raw[date_col], errors="coerce").dt.normalize()
    exact = raw.loc[dates.eq(requested)].copy()
    medians = pd.to_numeric(exact[median_col], errors="coerce")
    return {
        "requested_date": str(requested.date()),
        "row_count": int(len(raw)),
        "exact_date_row_count": int(len(exact)),
        "positive_static_median_count": int(medians.gt(0).sum()),
        "industry_code_count": int(exact[industry_code_col].astype(str).nunique()),
        "required_columns_present": True,
        "returned_dates": sorted(
            {
                str(pd.Timestamp(value).date())
                for value in dates.dropna().unique()
            }
        ),
    }


def deterministic_symbol_probe_sample(
    metadata: pd.DataFrame,
    *,
    per_board: int = 2,
) -> list[str]:
    required = {"symbol", "board", "listing_date"}
    missing = required - set(metadata.columns)
    if missing:
        raise ValueError(f"metadata missing columns: {sorted(missing)}")
    frame = metadata.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["listing_date"] = pd.to_datetime(
        frame["listing_date"], errors="raise"
    ).dt.normalize()

    symbols: list[str] = []
    for board in ("SSE_MAIN", "SZSE_MAIN", "STAR", "CHINEXT", "BSE"):
        part = frame.loc[frame["board"].eq(board)].sort_values(
            ["listing_date", "symbol"]
        )
        if part.empty:
            continue
        picks: list[str] = []
        if per_board >= 1:
            picks.append(str(part.iloc[0]["symbol"]))
        if per_board >= 2 and len(part) > 1:
            picks.append(str(part.iloc[-1]["symbol"]))
        if per_board > 2 and len(part) > 2:
            positions = [
                round(i * (len(part) - 1) / (per_board - 1))
                for i in range(per_board)
            ]
            picks = [str(part.iloc[pos]["symbol"]) for pos in positions]
        symbols.extend(picks)

    return list(dict.fromkeys(symbols))


def deterministic_filing_probe_sample(metadata: pd.DataFrame) -> list[str]:
    required = {"symbol", "board", "listing_date"}
    missing = required - set(metadata.columns)
    if missing:
        raise ValueError(f"metadata missing columns: {sorted(missing)}")
    frame = metadata.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["listing_date"] = pd.to_datetime(
        frame["listing_date"], errors="raise"
    ).dt.normalize()

    picks: list[str] = []
    for board in ("SSE_MAIN", "SZSE_MAIN", "STAR", "CHINEXT"):
        part = frame.loc[frame["board"].eq(board)].sort_values(
            ["listing_date", "symbol"], ascending=[False, True]
        )
        if not part.empty:
            picks.append(str(part.iloc[0]["symbol"]))
    return picks


def select_probe_trade_dates(
    benchmark: pd.DataFrame,
    *,
    as_of: object,
) -> list[pd.Timestamp]:
    if "date" not in benchmark.columns:
        raise ValueError("benchmark missing date")
    dates = (
        pd.DatetimeIndex(pd.to_datetime(benchmark["date"], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(dates):
        raise ValueError("benchmark date rail is empty")
    as_of_ts = pd.Timestamp(as_of).normalize()
    targets = [
        pd.Timestamp("2019-01-04"),
        pd.Timestamp("2022-01-04"),
        pd.Timestamp("2024-01-03"),
        pd.Timestamp("2026-01-05"),
        as_of_ts,
    ]
    selected: list[pd.Timestamp] = []
    for target in targets:
        eligible = dates[dates <= target]
        if len(eligible):
            selected.append(pd.Timestamp(eligible[-1]).normalize())
    return list(dict.fromkeys(selected))


def public_probe_boundary_check(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    forbidden = (
        "candidate_a_event",
        "candidate_b_eligibility",
        "forward_return",
        "benchmark_relative_return",
        "research_verdict",
        "portfolio_holding",
    )
    hits = [token for token in forbidden if token in serialized]
    if hits:
        raise ValueError(f"private/research semantics leaked into public probe: {hits}")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    public_probe_boundary_check(payload)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "CSRC_TAXONOMY_LABEL",
    "INDUSTRY_CHANGE_PROVIDER",
    "INDUSTRY_PE_PROVIDER",
    "deterministic_filing_probe_sample",
    "deterministic_symbol_probe_sample",
    "normalize_industry_change_probe",
    "normalize_industry_pe_probe",
    "public_probe_boundary_check",
    "retry_call",
    "select_probe_trade_dates",
    "write_json",
]
