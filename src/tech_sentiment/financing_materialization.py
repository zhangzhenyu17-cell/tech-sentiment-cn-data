from __future__ import annotations

from dataclasses import dataclass
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
    raw_aligned: pd.DataFrame
    canonical: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _pick(frame: pd.DataFrame, candidates: tuple[str, ...], label: str) -> str:
    for column in candidates:
        if column in frame.columns:
            return column
    raise ValueError(f"financing source missing {label}: {list(candidates)}")


def normalize_sse_margin_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "sse_financing_balance"])
    date_col = _pick(frame, ("信用交易日期", "交易日期", "日期", "trade_date", "date"), "SSE date")
    balance_col = _pick(frame, ("融资余额", "rzye", "financing_balance"), "SSE financing balance")
    out = frame[[date_col, balance_col]].copy()
    out.columns = ["date", "sse_financing_balance"]
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["sse_financing_balance"] = pd.to_numeric(out["sse_financing_balance"], errors="raise")
    if out["date"].duplicated().any():
        raise ValueError("SSE financing history has duplicate dates")
    if (out["sse_financing_balance"] <= 0).any():
        raise ValueError("SSE financing balance must be positive")
    return out.sort_values("date").reset_index(drop=True)


def normalize_szse_margin_history(
    frame: pd.DataFrame,
    *,
    observation_date: object | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "szse_financing_balance"])
    balance_col = _pick(frame, ("融资余额", "rzye", "financing_balance"), "SZSE financing balance")
    date_col = next(
        (column for column in ("融资融券交易日期", "交易日期", "日期", "trade_date", "date") if column in frame.columns),
        None,
    )
    if date_col is None:
        if observation_date is None or len(frame) != 1:
            raise ValueError("SZSE financing history without date requires one row and explicit observation_date")
        out = pd.DataFrame(
            {
                "date": [pd.Timestamp(observation_date).normalize()],
                "szse_financing_balance": [frame.iloc[0][balance_col]],
            }
        )
    else:
        out = frame[[date_col, balance_col]].copy()
        out.columns = ["date", "szse_financing_balance"]
        out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["szse_financing_balance"] = pd.to_numeric(out["szse_financing_balance"], errors="raise")
    if out["date"].duplicated().any():
        raise ValueError("SZSE financing history has duplicate dates")
    if (out["szse_financing_balance"] <= 0).any():
        raise ValueError("SZSE financing balance must be positive")
    return out.sort_values("date").reset_index(drop=True)


def _default_sse_fetch(start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak  # type: ignore

    return ak.stock_margin_sse(
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )


def _default_szse_range_fetch(start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak  # type: ignore

    return ak.stock_margin_szse(
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )


def _default_szse_date_fetch(date: str) -> pd.DataFrame:
    import akshare as ak  # type: ignore

    return ak.stock_margin_szse(date=date.replace("-", ""))


def materialize_financing_history(
    *,
    trading_dates: Iterable[object],
    start_date: str,
    end_date: str,
    sse_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
    szse_range_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
    szse_date_fetcher: Callable[[str], pd.DataFrame] | None = None,
) -> FinancingMaterializationResult:
    """Materialize bilateral financing data under the frozen unit contract.

    SSE source values are CNY. SZSE source values are CNY_100M. This function
    never infers a multiplier from observed magnitude. Missing exchange days
    remain explicit and prevent QUALIFIED_INPUT.
    """

    calendar = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(calendar):
        raise ValueError("trading_dates cannot be empty")
    sse_fetch = sse_fetcher or _default_sse_fetch
    sz_range = szse_range_fetcher or _default_szse_range_fetch
    sz_date = szse_date_fetcher or _default_szse_date_fetch
    errors: list[dict[str, str]] = []

    try:
        sse = normalize_sse_margin_history(sse_fetch(start_date, end_date))
    except Exception as exc:
        errors.append({"exchange": "SSE", "date": "RANGE", "error": f"{type(exc).__name__}: {exc}"})
        sse = pd.DataFrame(columns=["date", "sse_financing_balance"])

    try:
        szse = normalize_szse_margin_history(sz_range(start_date, end_date))
    except TypeError:
        parts: list[pd.DataFrame] = []
        for day in calendar:
            value = str(pd.Timestamp(day).date())
            try:
                normalized = normalize_szse_margin_history(
                    sz_date(value), observation_date=day
                )
                if len(normalized):
                    parts.append(normalized)
            except Exception as exc:
                errors.append(
                    {"exchange": "SZSE", "date": value, "error": f"{type(exc).__name__}: {exc}"}
                )
        szse = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["date", "szse_financing_balance"]
        )
    except Exception as exc:
        errors.append({"exchange": "SZSE", "date": "RANGE", "error": f"{type(exc).__name__}: {exc}"})
        szse = pd.DataFrame(columns=["date", "szse_financing_balance"])

    aligned = pd.DataFrame({"date": calendar})
    aligned = aligned.merge(sse, on="date", how="left", validate="one_to_one")
    aligned = aligned.merge(szse, on="date", how="left", validate="one_to_one")
    aligned["sse_source_unit"] = "CNY"
    aligned["szse_source_unit"] = "CNY_100M"
    aligned["sse_source_identity"] = SSE_MARGIN_SOURCE_ID
    aligned["szse_source_identity"] = SZSE_MARGIN_SOURCE_ID
    aligned["sse_source_url"] = SSE_MARGIN_SOURCE_URL
    aligned["szse_source_url"] = SZSE_MARGIN_SOURCE_URL
    aligned["provider"] = "SSE+SZSE_OFFICIAL_VIA_AKSHARE"
    aligned["provenance"] = "official exchange margin-summary historical interfaces; frozen source-unit contract"

    bilateral = aligned[["sse_financing_balance", "szse_financing_balance"]].notna().all(axis=1)
    bilateral_days = int(bilateral.sum())
    coverage = float(bilateral.mean()) if len(aligned) else 0.0
    missing_days = int((~bilateral).sum())
    if not bilateral.all():
        return FinancingMaterializationResult(
            raw_aligned=aligned,
            canonical=pd.DataFrame(),
            errors=pd.DataFrame(errors, columns=["exchange", "date", "error"]),
            summary={
                "state": "PARTIAL_COVERAGE",
                "role": "RESEARCH_INPUT",
                "trading_days": int(len(aligned)),
                "bilateral_complete_days": bilateral_days,
                "bilateral_coverage": coverage,
                "missing_days": missing_days,
                "sse_raw_unit": "CNY",
                "szse_raw_unit": "CNY_100M",
                "canonical_unit": "CNY",
                "multiplier_inferred_from_values": False,
                "included_in_capital_regime_composite": False,
            },
        )

    qualified, unit_summary = qualify_financing_yuan(
        aligned[
            [
                "date",
                "sse_financing_balance",
                "szse_financing_balance",
                "sse_source_unit",
                "szse_source_unit",
            ]
        ]
    )
    state = "QUALIFIED_INPUT" if unit_summary.get("state") == "CANONICAL_UNIT_QUALIFIED" else "DATA_INSUFFICIENT"
    summary = {
        **unit_summary,
        "state": state,
        "role": "RESEARCH_INPUT",
        "trading_days": int(len(aligned)),
        "bilateral_complete_days": bilateral_days,
        "bilateral_coverage": coverage,
        "missing_days": missing_days,
        "multiplier_inferred_from_values": False,
        "included_in_capital_regime_composite": False,
    }
    return FinancingMaterializationResult(
        raw_aligned=aligned,
        canonical=qualified,
        errors=pd.DataFrame(errors, columns=["exchange", "date", "error"]),
        summary=summary,
    )


__all__ = [
    "FinancingMaterializationResult",
    "normalize_sse_margin_history",
    "normalize_szse_margin_history",
    "materialize_financing_history",
]
