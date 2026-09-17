from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd


SECTOR_INDEX_EASTMONEY_SECIDS = {
    "931152": "2.931152",
}
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _new_eastmoney_session() -> Any:
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("requests is required for direct sector index history") from exc

    current = requests.Session()
    current.headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"
            ),
            "Connection": "close",
        }
    )
    transport_retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    current.mount("https://", HTTPAdapter(max_retries=transport_retry))
    return current


def fetch_sector_index_history_direct(
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
    """Fetch a preregistered sector index without provider-wide code discovery.

    EastMoney's generic AKShare index adapter first downloads a broad code map.
    That discovery endpoint is unnecessary for a frozen, known index and can fail
    independently of the actual kline endpoint.  This adapter pins the audited
    market identifier for 931152 and records it in the returned provenance.

    When no caller-managed session is supplied, every outer attempt uses a fresh
    HTTP session with bounded transport retries.  This avoids reusing a poisoned
    keep-alive connection while preserving the same provider, identifier and
    fail-closed data semantics.
    """
    code = str(index_code).strip().zfill(6)
    secid = SECTOR_INDEX_EASTMONEY_SECIDS.get(code)
    if secid is None:
        raise ValueError(f"no fixed EastMoney secid configured for sector index {code}")
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    params = {
        "secid": secid,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": str(start_date).replace("-", ""),
        "end": str(end_date).replace("-", ""),
    }

    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(retries + 1):
        current_session = session if session is not None else _new_eastmoney_session()
        owns_session = session is None
        try:
            response = current_session.get(
                EASTMONEY_KLINE_URL,
                params=params,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            candidate = response.json()
            data = candidate.get("data") if isinstance(candidate, dict) else None
            klines = data.get("klines") if isinstance(data, dict) else None
            if not klines:
                raise RuntimeError(f"EastMoney returned no kline data for {code} via {secid}")
            returned_code = str(data.get("code", code)).strip()
            if returned_code and returned_code != code:
                raise RuntimeError(
                    f"EastMoney secid {secid} resolved to unexpected code {returned_code}"
                )
            payload = candidate
            break
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            if retry_backoff_seconds:
                sleep_fn(retry_backoff_seconds * (attempt + 1))
        finally:
            if owns_session:
                current_session.close()

    if payload is None:
        raise RuntimeError(
            f"direct EastMoney sector index history failed for {code} via {secid}"
        ) from last_error

    data = payload["data"]
    raw = pd.DataFrame(
        [str(item).split(",") for item in data["klines"]],
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
    raw["date"] = pd.to_datetime(raw["date"], errors="raise").dt.normalize()
    for column in ("open", "close", "high", "low", "volume", "amount", "pct_chg"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    out = raw[(raw["date"] >= start) & (raw["date"] <= end)].copy()
    out.insert(1, "index_code", code)
    out["provider"] = "eastmoney:direct_sector_index_kline"
    out["provider_identifier"] = secid
    columns = [
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
        "provider_identifier",
    ]
    return out.loc[:, columns].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
