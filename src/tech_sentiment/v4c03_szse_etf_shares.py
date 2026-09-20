from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import time
from typing import Any, Callable, Iterable

import pandas as pd

from .bounded_retry import call_with_bounded_network_retry


SZSE_ETF_SHARE_SOURCE_ID = "SZSE_ETF_SCALE_DAILY"
SZSE_ETF_SHARE_PROVIDER = "akshare:fund_scale_daily_szse"


@dataclass(frozen=True)
class SzseEtfShareFetchResult:
    data: pd.DataFrame
    errors: pd.DataFrame


def _client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "AKShare is not installed. Install the data extra with: pip install -e '.[data]'"
        ) from exc
    return ak


def _chunk_ranges(start_date: str, end_date: str, *, days: int = 180):
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def normalize_szse_etf_share_history(
    raw: pd.DataFrame,
    *,
    fund_codes: Iterable[str],
    trading_dates: Iterable[object],
) -> pd.DataFrame:
    required = {"日期", "基金代码", "基金份额"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"SZSE ETF history missing columns: {sorted(missing)}")
    wanted = {str(code).zfill(6) for code in fund_codes}
    calendar = set(
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .to_pydatetime()
    )
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["日期"], errors="coerce").dt.normalize(),
            "fund_code": (
                raw["基金代码"].astype(str)
                .str.extract(r"(\d+)", expand=False)
                .str.zfill(6)
            ),
            "fund_shares": pd.to_numeric(raw["基金份额"], errors="coerce"),
        }
    )
    out = out[
        out["fund_code"].isin(wanted)
        & out["date"].notna()
        & out["date"].map(lambda x: x.to_pydatetime() in calendar)
    ].copy()
    if out.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "fund_code",
                "fund_shares",
                "unit",
                "source_identity",
                "provider",
                "evidence_available_date",
            ]
        )
    if out["fund_shares"].isna().any() or (out["fund_shares"] <= 0).any():
        raise ValueError("SZSE ETF history contains invalid fund shares")
    if out.duplicated(["date", "fund_code"]).any():
        raise ValueError("SZSE ETF history contains duplicate date/fund rows")
    out["unit"] = "share"
    out["source_identity"] = SZSE_ETF_SHARE_SOURCE_ID
    out["provider"] = SZSE_ETF_SHARE_PROVIDER
    out["evidence_available_date"] = out["date"]
    return out[
        [
            "date",
            "fund_code",
            "fund_shares",
            "unit",
            "source_identity",
            "provider",
            "evidence_available_date",
        ]
    ].sort_values(["date", "fund_code"]).reset_index(drop=True)


def fetch_szse_etf_share_history(
    *,
    start_date: str,
    end_date: str,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    retries: int = 3,
    retry_backoff_seconds: float = 0.6,
    sleep_seconds: float = 0.05,
    client: Any | None = None,
) -> SzseEtfShareFetchResult:
    dates = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    codes = tuple(sorted({str(code).zfill(6) for code in fund_codes}))
    if not len(dates):
        raise ValueError("trading_dates cannot be empty")
    if not codes:
        raise ValueError("at least one fund code is required")

    ak = _client(client)
    parts: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    for chunk_start, chunk_end in _chunk_ranges(start_date, end_date):
        chunk_dates = dates[(dates >= chunk_start) & (dates <= chunk_end)]
        if not len(chunk_dates):
            continue
        try:
            raw = call_with_bounded_network_retry(
                lambda cs=chunk_start, ce=chunk_end: ak.fund_scale_daily_szse(
                    start_date=cs.strftime("%Y%m%d"),
                    end_date=ce.strftime("%Y%m%d"),
                    symbol="ETF",
                ),
                attempts=retries,
                backoff_seconds=retry_backoff_seconds,
            )
            if raw is None or len(raw) == 0:
                raise RuntimeError("SZSE ETF source returned no rows")
            normalized = normalize_szse_etf_share_history(
                raw,
                fund_codes=codes,
                trading_dates=chunk_dates,
            )
            if len(normalized):
                parts.append(normalized)
        except Exception as exc:
            errors.append(
                {
                    "chunk_start": str(chunk_start.date()),
                    "chunk_end": str(chunk_end.date()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    data = (
        pd.concat(parts, ignore_index=True, sort=False)
        if parts
        else pd.DataFrame(
            columns=[
                "date",
                "fund_code",
                "fund_shares",
                "unit",
                "source_identity",
                "provider",
                "evidence_available_date",
            ]
        )
    )
    if len(data):
        data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
        data = data.sort_values(["date", "fund_code"]).drop_duplicates(
            ["date", "fund_code"], keep="last"
        ).reset_index(drop=True)
    return SzseEtfShareFetchResult(
        data=data,
        errors=pd.DataFrame(errors, columns=["chunk_start", "chunk_end", "error"]),
    )


__all__ = [
    "SZSE_ETF_SHARE_SOURCE_ID",
    "SZSE_ETF_SHARE_PROVIDER",
    "SzseEtfShareFetchResult",
    "normalize_szse_etf_share_history",
    "fetch_szse_etf_share_history",
]
