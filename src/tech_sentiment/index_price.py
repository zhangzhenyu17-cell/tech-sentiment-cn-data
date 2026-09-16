from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd


_TENCENT_INDEX_SYMBOLS = {
    "000688": "sh000688",  # STAR 50
    "399673": "sz399673",  # ChiNext 50
}

_DIRECT_EASTMONEY_SECIDS = {
    "000688": "1.000688",  # STAR 50, Shanghai
    "399673": "0.399673",  # ChiNext 50, Shenzhen
}


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


def _empty_index_history() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date",
            "index_code",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_chg",
            "provider",
        ]
    )


def _normalize_index_history(
    raw: pd.DataFrame,
    *,
    code: str,
    start_date: str,
    end_date: str,
    provider: str,
) -> pd.DataFrame:
    if raw is None or len(raw) == 0:
        return _empty_index_history()

    aliases = {
        "date": ("日期", "date"),
        "open": ("开盘", "open"),
        "close": ("收盘", "close"),
        "high": ("最高", "high"),
        "low": ("最低", "low"),
        "volume": ("成交量", "volume"),
        "amount": ("成交额", "amount"),
        "pct_chg": ("涨跌幅", "pct_chg"),
    }
    selected: dict[str, pd.Series] = {}
    for target, candidates in aliases.items():
        source = next((c for c in candidates if c in raw.columns), None)
        if source is None:
            if target in {"volume", "amount", "pct_chg"}:
                continue
            raise ValueError(
                f"index history for {code} is missing {target}; columns={list(raw.columns)}"
            )
        selected[target] = raw[source]

    out = pd.DataFrame(selected)
    out.insert(1, "index_code", code)
    out["date"] = pd.to_datetime(out["date"], errors="raise")
    for column in ("open", "close", "high", "low", "volume", "amount", "pct_chg"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    if "pct_chg" not in out.columns:
        out["pct_chg"] = out["close"].pct_change(fill_method=None) * 100.0

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    out = out[(out["date"] >= start) & (out["date"] <= end)].copy()
    out["provider"] = provider
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _fetch_tencent_index(
    code: str,
    *,
    start_date: str,
    end_date: str,
    client: Any | None = None,
    retries: int = 2,
    retry_backoff_seconds: float = 0.75,
    timeout_seconds: float = 15.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fetch a formal index through the Tencent adapter already used for stocks."""
    if code not in _TENCENT_INDEX_SYMBOLS:
        raise ValueError(f"no Tencent symbol configured for index {code}")
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    ak = _client(client)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            raw = ak.stock_zh_a_hist_tx(
                symbol=_TENCENT_INDEX_SYMBOLS[code],
                start_date=start_date,
                end_date=end_date,
                adjust="",
                timeout=timeout_seconds,
            )
            if raw is None or len(raw) == 0:
                raise RuntimeError(f"Tencent returned no index history for {code}")
            return raw
        except Exception as exc:  # network/provider failures are retryable here
            last_error = exc
            if attempt >= retries:
                break
            sleep_fn(retry_backoff_seconds * (attempt + 1))

    raise RuntimeError(
        f"Tencent index history failed for {code} after {retries + 1} attempts"
    ) from last_error


def _fetch_eastmoney_direct(
    code: str,
    *,
    start_date: str,
    end_date: str,
    session: Any | None = None,
    retries: int = 1,
    retry_backoff_seconds: float = 0.75,
    timeout_seconds: float = 10.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fallback fetch that bypasses Eastmoney's market-code discovery call."""
    if code not in _DIRECT_EASTMONEY_SECIDS:
        raise ValueError(f"no direct Eastmoney secid configured for index {code}")
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
            raise RuntimeError("requests is required for direct index history fetches") from exc
        session = requests.Session()

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": _DIRECT_EASTMONEY_SECIDS[code],
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": str(start_date).replace("-", ""),
        "end": str(end_date).replace("-", ""),
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            klines = data.get("klines") if isinstance(data, dict) else None
            if not klines:
                raise RuntimeError(f"Eastmoney returned no kline data for {code}")
            return pd.DataFrame(
                [str(item).split(",") for item in klines],
                columns=[
                    "date",
                    "open",
                    "close",
                    "high",
                    "low",
                    "volume",
                    "amount",
                    "amplitude",
                    "pct_chg",
                    "change",
                    "turnover",
                ],
            )
        except Exception as exc:  # network/HTTP/JSON/provider failures are retryable here
            last_error = exc
            if attempt >= retries:
                break
            sleep_fn(retry_backoff_seconds * (attempt + 1))

    raise RuntimeError(
        f"direct Eastmoney index history failed for {code} after {retries + 1} attempts"
    ) from last_error


def fetch_index_history(
    index_code: str,
    *,
    start_date: str,
    end_date: str,
    client: Any | None = None,
    session: Any | None = None,
    retries: int = 2,
    retry_backoff_seconds: float = 0.75,
    timeout_seconds: float = 15.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """Fetch and normalize an official A-share index daily price rail.

    Formal STAR 50 and ChiNext 50 research uses Tencent first because that same
    channel already serves the point-in-time constituent downloads reliably in
    GitHub Actions. A fixed-secid Eastmoney request is retained only as fallback.
    Unknown index codes keep the legacy AKShare index interface for compatibility.
    """
    code = str(index_code).strip().zfill(6)

    if code in _TENCENT_INDEX_SYMBOLS:
        try:
            raw = _fetch_tencent_index(
                code,
                start_date=start_date,
                end_date=end_date,
                client=client,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                timeout_seconds=timeout_seconds,
                sleep_fn=sleep_fn,
            )
            provider = "akshare:tencent_index"
        except Exception as tencent_error:
            try:
                raw = _fetch_eastmoney_direct(
                    code,
                    start_date=start_date,
                    end_date=end_date,
                    session=session,
                    retries=1,
                    retry_backoff_seconds=retry_backoff_seconds,
                    timeout_seconds=min(timeout_seconds, 10.0),
                    sleep_fn=sleep_fn,
                )
                provider = "eastmoney:direct_index_kline"
            except Exception as eastmoney_error:
                raise RuntimeError(
                    f"all formal index providers failed for {code}: "
                    f"Tencent={tencent_error}; Eastmoney={eastmoney_error}"
                ) from eastmoney_error
    else:
        ak = _client(client)
        raw = ak.index_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=str(start_date).replace("-", ""),
            end_date=str(end_date).replace("-", ""),
        )
        provider = "akshare:index_zh_a_hist"

    return _normalize_index_history(
        raw,
        code=code,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )


def merge_index_price_rail(
    sentiment: pd.DataFrame,
    index_prices: pd.DataFrame,
    *,
    price_col: str = "index_close",
    require_complete: bool = True,
) -> pd.DataFrame:
    """Merge one official index close series onto a sentiment history by date.

    Formal top-risk research should normally set ``require_complete=True`` so a
    missing official index observation fails closed instead of silently falling
    back to the internal equal-weight rail.
    """
    if "date" not in sentiment.columns:
        raise ValueError("sentiment is missing date")
    required = {"date", "close"}
    missing = required - set(index_prices.columns)
    if missing:
        raise ValueError(f"index_prices missing required columns: {sorted(missing)}")
    if price_col in sentiment.columns:
        raise ValueError(f"sentiment already contains price rail column: {price_col}")

    left = sentiment.copy()
    right = index_prices.copy()
    left["date"] = pd.to_datetime(left["date"], errors="raise")
    right["date"] = pd.to_datetime(right["date"], errors="raise")
    if right["date"].duplicated().any():
        raise ValueError("index_prices contains duplicate dates")

    rail = right[["date", "close"]].rename(columns={"close": price_col})
    merged = left.merge(rail, on="date", how="left", validate="one_to_one")
    if require_complete and merged[price_col].isna().any():
        missing_dates = merged.loc[merged[price_col].isna(), "date"].dt.strftime("%Y-%m-%d")
        preview = ", ".join(missing_dates.head(5))
        raise ValueError(f"official index price rail missing sentiment dates: {preview}")
    return merged

