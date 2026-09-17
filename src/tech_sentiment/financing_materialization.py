from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Iterable

import pandas as pd

from .capital_input_data import (
    SSE_MARGIN_SOURCE_ID,
    SSE_MARGIN_SOURCE_URL,
    SZSE_MARGIN_SOURCE_ID,
    SZSE_MARGIN_SOURCE_URL,
    qualify_financing_yuan,
)


@dataclass(frozen=True)
class FinancingMaterializationResult:
    raw: pd.DataFrame
    canonical: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _calendar(values: Iterable[object]) -> pd.DatetimeIndex:
    cal = pd.DatetimeIndex(pd.to_datetime(list(values), errors="raise")).normalize()
    if not len(cal) or cal.has_duplicates:
        raise ValueError("trading calendar must be non-empty and unique")
    return cal.sort_values()


def _sha256_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_sse_financing_history(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"信用交易日期", "融资余额"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SSE financing history missing columns: {sorted(missing)}")
    out = frame[["信用交易日期", "融资余额"]].copy()
    out["date"] = pd.to_datetime(out["信用交易日期"], errors="raise").dt.normalize()
    out["sse_financing_balance"] = pd.to_numeric(out["融资余额"], errors="coerce")
    if out["date"].duplicated().any():
        raise ValueError("SSE financing history contains duplicate dates")
    if out["sse_financing_balance"].isna().any() or (out["sse_financing_balance"] <= 0).any():
        raise ValueError("SSE financing history contains invalid balances")
    out["sse_source_unit"] = "CNY"
    out["sse_source_identity"] = SSE_MARGIN_SOURCE_ID
    out["sse_source_url"] = SSE_MARGIN_SOURCE_URL
    return out[
        [
            "date",
            "sse_financing_balance",
            "sse_source_unit",
            "sse_source_identity",
            "sse_source_url",
        ]
    ].sort_values("date").reset_index(drop=True)


def normalize_szse_financing_snapshot(
    frame: pd.DataFrame, *, observation_date: object
) -> dict[str, object]:
    if "融资余额" not in frame.columns or len(frame) != 1:
        raise ValueError("SZSE financing snapshot requires exactly one 融资余额 row")
    value = pd.to_numeric(pd.Series([frame.iloc[0]["融资余额"]]), errors="coerce").iloc[0]
    if pd.isna(value) or float(value) <= 0:
        raise ValueError("SZSE financing snapshot contains invalid balance")
    return {
        "date": pd.Timestamp(observation_date).normalize(),
        "szse_financing_balance": float(value),
        "szse_source_unit": "CNY_100M",
        "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
        "szse_source_url": SZSE_MARGIN_SOURCE_URL,
    }


def materialize_financing_history(
    trading_dates: Iterable[object],
    *,
    sse_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
    szse_fetcher: Callable[[str], pd.DataFrame] | None = None,
) -> FinancingMaterializationResult:
    """Materialize a bilateral financing rail without guessing units or missing dates.

    The frozen source-unit contract is SSE=CNY and SZSE=CNY_100M.  The function
    preserves every target trading date in the raw rail.  A date is eligible for
    qualification only when both exchanges are present on that exact date.
    """

    cal = _calendar(trading_dates)
    if sse_fetcher is None or szse_fetcher is None:
        import akshare as ak  # type: ignore

        if sse_fetcher is None:
            sse_fetcher = lambda start, end: ak.stock_margin_sse(
                start_date=start, end_date=end
            )
        if szse_fetcher is None:
            szse_fetcher = lambda date: ak.stock_margin_szse(date=date)

    errors: list[dict[str, str]] = []
    start_arg = cal.min().strftime("%Y%m%d")
    end_arg = cal.max().strftime("%Y%m%d")
    try:
        sse = normalize_sse_financing_history(sse_fetcher(start_arg, end_arg))
        sse = sse[sse["date"].isin(cal)].copy()
    except Exception as exc:
        sse = pd.DataFrame(
            columns=[
                "date",
                "sse_financing_balance",
                "sse_source_unit",
                "sse_source_identity",
                "sse_source_url",
            ]
        )
        errors.append({"date": "RANGE", "exchange": "SSE", "error": f"{type(exc).__name__}: {exc}"})

    sz_rows: list[dict[str, object]] = []
    for date in cal:
        arg = pd.Timestamp(date).strftime("%Y%m%d")
        try:
            sz_rows.append(
                normalize_szse_financing_snapshot(
                    szse_fetcher(arg), observation_date=date
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "date": str(pd.Timestamp(date).date()),
                    "exchange": "SZSE",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    szse = pd.DataFrame(sz_rows)

    raw = pd.DataFrame({"date": cal})
    raw = raw.merge(sse, on="date", how="left", validate="one_to_one")
    raw = raw.merge(szse, on="date", how="left", validate="one_to_one")
    raw["bilateral_complete"] = raw[
        ["sse_financing_balance", "szse_financing_balance"]
    ].notna().all(axis=1)

    complete = raw.loc[raw["bilateral_complete"]].copy()
    canonical, qualification = qualify_financing_yuan(complete)
    captured_at = datetime.now(timezone.utc).isoformat()
    source_query = {
        "calendar_start": str(cal.min().date()),
        "calendar_end": str(cal.max().date()),
        "target_days": int(len(cal)),
        "sse_source_identity": SSE_MARGIN_SOURCE_ID,
        "sse_source_url": SSE_MARGIN_SOURCE_URL,
        "sse_raw_unit": "CNY",
        "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
        "szse_source_url": SZSE_MARGIN_SOURCE_URL,
        "szse_raw_unit": "CNY_100M",
        "canonical_unit": "CNY",
    }
    summary: dict[str, object] = {
        **source_query,
        "captured_at_utc": captured_at,
        "source_query_identity": _sha256_payload(source_query),
        "bilateral_complete_days": int(raw["bilateral_complete"].sum()),
        "bilateral_coverage": float(raw["bilateral_complete"].mean()),
        "error_rows": int(len(errors)),
        "qualification_state": qualification.get("state", "DATA_INSUFFICIENT"),
        "median_scale_ratio": qualification.get("median_scale_ratio"),
        "role": "RESEARCH_INPUT",
        "included_in_capital_regime_composite": False,
        "no_unit_inference_from_anomaly": True,
    }
    return FinancingMaterializationResult(
        raw=raw,
        canonical=canonical,
        errors=pd.DataFrame(errors, columns=["date", "exchange", "error"]),
        summary=summary,
    )


__all__ = [
    "FinancingMaterializationResult",
    "normalize_sse_financing_history",
    "normalize_szse_financing_snapshot",
    "materialize_financing_history",
]
