from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd


CSINDEX_INDEX_PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CSI_ARRAY_COLUMNS = (
    "date",
    "index_code",
    "index_name_cn",
    "index_short_name_cn",
    "index_name_en",
    "index_short_name_en",
    "open",
    "high",
    "low",
    "close",
    "change",
    "pct_chg",
    "volume",
    "amount",
    "sample_count",
    "rolling_pe",
)


def _normalise_csindex_rows(data: list[Any], code: str) -> pd.DataFrame:
    """Normalise the official CSI index-perf payload without guessing values.

    The live endpoint currently returns each observation as a positional 16-field
    array, which is also how AKShare's stock_zh_index_hist_csindex adapter parses
    it.  Mapping-shaped rows are accepted as a defensive compatibility path, but
    only documented aliases are recognised and missing required fields fail closed.
    """
    if not data:
        raise ValueError(f"CSI returned no history for {code}")

    first = data[0]
    if isinstance(first, (list, tuple)):
        if any(not isinstance(row, (list, tuple)) or len(row) != len(CSI_ARRAY_COLUMNS) for row in data):
            raise ValueError("CSI index-perf positional rows do not have the expected 16 fields")
        raw = pd.DataFrame(data, columns=CSI_ARRAY_COLUMNS)
        out = raw[[
            "date",
            "index_code",
            "open",
            "high",
            "low",
            "close",
            "pct_chg",
            "volume",
            "amount",
        ]].copy()
    elif isinstance(first, dict):
        raw = pd.DataFrame(data)
        aliases = {
            "date": ("tradeDate", "date", "日期"),
            "index_code": ("indexCode", "指数代码"),
            "open": ("open", "开盘"),
            "high": ("high", "最高"),
            "low": ("low", "最低"),
            "close": ("close", "收盘"),
            "pct_chg": ("pctChange", "涨跌幅"),
            "volume": ("volume", "成交量"),
            "amount": ("turnover", "amount", "成交金额"),
        }
        selected: dict[str, pd.Series] = {}
        for target, candidates in aliases.items():
            source = next((name for name in candidates if name in raw.columns), None)
            if source is None:
                if target in {"open", "high", "low", "pct_chg", "volume", "amount"}:
                    continue
                raise ValueError(
                    f"CSI history for {code} is missing required {target}; columns={list(raw.columns)}"
                )
            selected[target] = raw[source]
        out = pd.DataFrame(selected)
    else:
        raise ValueError(f"unsupported CSI index-perf row type: {type(first).__name__}")

    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    if "index_code" not in out.columns:
        out["index_code"] = code
    out["index_code"] = out["index_code"].astype(str).str.strip().str.zfill(6)
    if set(out["index_code"]) != {code}:
        raise ValueError(f"CSI history returned unexpected index codes: {sorted(set(out['index_code']))}")
    for column in ("open", "high", "low", "close", "pct_chg", "volume", "amount"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if out["close"].isna().any():
        raise ValueError("CSI official history contains null close values")
    return out


def fetch_csindex_history(
    index_code: str,
    *,
    start_date: str,
    end_date: str,
    session: Any | None = None,
    retries: int = 2,
    retry_backoff_seconds: float = 0.75,
    timeout_seconds: float = 15.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fetch a CSI index history directly from the official index-perf endpoint."""
    code = str(index_code).strip().zfill(6)
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    if session is None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("requests is required for CSI index history") from exc
        session = requests.Session()

    params = {
        "indexCode": code,
        "startDate": str(start_date).replace("-", ""),
        "endDate": str(end_date).replace("-", ""),
    }
    last_error: Exception | None = None
    data: list[Any] | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(CSINDEX_INDEX_PERF_URL, params=params, timeout=timeout_seconds)
            response.raise_for_status()
            candidate = response.json()
            candidate_data = candidate.get("data") if isinstance(candidate, dict) else None
            if not isinstance(candidate_data, list) or not candidate_data:
                raise RuntimeError(f"CSI returned no history for {code}")
            data = candidate_data
            break
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_fn(retry_backoff_seconds * (attempt + 1))

    if data is None:
        raise RuntimeError(f"CSI official history failed for {code}") from last_error

    out = _normalise_csindex_rows(data, code)
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    out = out[(out["date"] >= start) & (out["date"] <= end)].copy()
    if out.empty:
        raise ValueError(f"CSI history has no rows inside requested interval for {code}")
    out["provider"] = "csindex:index_perf"
    out["provider_identifier"] = code
    if out["date"].duplicated().any():
        raise ValueError("CSI official history contains duplicate dates")
    return out.sort_values("date").reset_index(drop=True)
