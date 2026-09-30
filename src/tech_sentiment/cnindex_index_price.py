from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd


CNINDEX_DAILY_URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyData"


def fetch_cnindex_history(
    index_code: str,
    *,
    start_date: str,
    end_date: str,
    session: Any | None = None,
    retries: int = 2,
    retry_backoff_seconds: float = 0.75,
    timeout_seconds: float = 20.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fetch official CNI/SZSE index daily price history.

    This endpoint provides price/turnover history, but not a historical index
    valuation series. rolling_pe is therefore intentionally emitted as
    missing rather than reconstructed or backfilled from a current snapshot.
    """
    code = str(index_code).strip().zfill(6)
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    if session is None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("requests is required for CNI index history") from exc
        session = requests.Session()

    headers = {
        "Accept": "application/json,text/javascript,*/*;q=0.01",
        "Origin": "https://www.cnindex.com.cn",
        "Referer": "https://www.cnindex.com.cn/",
        "User-Agent": "Mozilla/5.0",
    }
    params = {
        "indexCode": code,
        "startDate": str(start_date),
        "endDate": str(end_date),
    }

    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(
                CNINDEX_DAILY_URL,
                params=params,
                headers=headers,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            candidate = response.json()
            data = candidate.get("data") if isinstance(candidate, dict) else None
            rows = data.get("data") if isinstance(data, dict) else None
            items = data.get("item") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                raise RuntimeError(f"CNI returned no daily history for {code}")
            if not isinstance(items, list) or not items:
                raise RuntimeError(f"CNI daily history missing item schema for {code}")
            returned_code = str(data.get("indexCode") or code).strip().zfill(6)
            if returned_code != code:
                raise RuntimeError(
                    f"CNI requested {code} but returned indexCode={returned_code}"
                )
            payload = candidate
            break
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_fn(retry_backoff_seconds * (attempt + 1))

    if payload is None:
        raise RuntimeError(f"CNI official history failed for {code}") from last_error

    data = payload["data"]
    items = [str(x) for x in data["item"]]
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "percent",
        "amount",
        "volume",
    }
    missing = required - set(items)
    if missing:
        raise ValueError(f"CNI daily schema missing fields: {sorted(missing)}")
    if any(not isinstance(row, (list, tuple)) or len(row) != len(items) for row in data["data"]):
        raise ValueError("CNI daily row length does not match item schema")

    raw = pd.DataFrame(data["data"], columns=items)
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["timestamp"], unit="ms", utc=True, errors="raise")
            .dt.tz_convert(None)
            .dt.normalize(),
            "index_code": code,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "pct_chg": pd.to_numeric(raw["percent"], errors="coerce") * 100.0,
            "volume": pd.to_numeric(raw["volume"], errors="coerce"),
            "amount": pd.to_numeric(raw["amount"], errors="coerce"),
            "sample_count": pd.NA,
            "rolling_pe": pd.NA,
            "provider": "cnindex:official_market_daily",
            "provider_identifier": code,
            "valuation_source_state": "OFFICIAL_HISTORICAL_VALUATION_UNAVAILABLE",
        }
    )
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    out = out[(out["date"] >= start) & (out["date"] <= end)].copy()
    if out.empty:
        raise ValueError(f"CNI history has no rows inside requested interval for {code}")
    if out["date"].duplicated().any():
        raise ValueError("CNI official history contains duplicate dates")
    if out["close"].isna().any() or (out["close"] <= 0).any():
        raise ValueError("CNI official history contains invalid close values")
    return out.sort_values("date").reset_index(drop=True)


__all__ = ["CNINDEX_DAILY_URL", "fetch_cnindex_history"]
