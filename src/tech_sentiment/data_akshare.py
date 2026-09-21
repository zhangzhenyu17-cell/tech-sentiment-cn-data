from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import time
from typing import Any

import pandas as pd

from .universe import infer_board, normalize_symbol, normalize_universe


@dataclass(frozen=True)
class DownloadResult:
    prices: pd.DataFrame
    errors: pd.DataFrame


def _client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra
        raise RuntimeError(
            "AKShare is not installed. Install the data extra with: pip install -e '.[data]'"
        ) from exc
    return ak


def _is_retryable_request_error(exc: BaseException) -> bool:
    """Return True only for transient HTTP/client transport failures.

    Keep the requests dependency lazy because the base package intentionally
    installs without the optional data extra.
    """
    try:
        from requests.exceptions import RequestException
    except ImportError:  # pragma: no cover - requests is part of the data extra
        return False
    return isinstance(exc, RequestException)


def fetch_current_csindex_universe(
    index_codes: Iterable[str],
    *,
    retries: int = 2,
    retry_backoff_seconds: float = 1.0,
    client: Any | None = None,
) -> pd.DataFrame:
    """Fetch the latest constituents for one or more CSI index codes.

    Important: AKShare's ``index_stock_cons_csindex`` endpoint exposes the latest
    constituent snapshot. The returned table is therefore labelled
    ``current_snapshot`` and must not be treated as point-in-time historical
    membership for a formal backtest.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")

    ak = _client(client)
    frames: list[pd.DataFrame] = []

    for raw_code in index_codes:
        index_code = str(raw_code).strip().zfill(6)
        raw = None
        for attempt in range(retries + 1):
            try:
                raw = ak.index_stock_cons_csindex(symbol=index_code)
                break
            except Exception as exc:
                if not _is_retryable_request_error(exc) or attempt >= retries:
                    raise
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * (attempt + 1))
        if raw is None or len(raw) == 0:
            continue

        code_col = "成分券代码" if "成分券代码" in raw.columns else "品种代码"
        name_col = "成分券名称" if "成分券名称" in raw.columns else None
        if code_col not in raw.columns:
            raise ValueError(
                f"AKShare constituent response for {index_code} has no recognized code column: "
                f"{list(raw.columns)}"
            )

        frame = pd.DataFrame({"symbol": raw[code_col].map(normalize_symbol)})
        if name_col is not None:
            frame["name"] = raw[name_col].astype(str)
        frame["source_index"] = index_code
        frame["board"] = frame["symbol"].map(infer_board)
        frame["universe_mode"] = "current_snapshot"
        frames.append(frame)

    if not frames:
        return pd.DataFrame(
            columns=["symbol", "name", "source_index", "board", "universe_mode"]
        )

    combined = pd.concat(frames, ignore_index=True)
    aggregation: dict[str, Any] = {
        "source_index": lambda s: ",".join(sorted(set(map(str, s)))),
        "board": "first",
        "universe_mode": "first",
    }
    if "name" in combined.columns:
        aggregation["name"] = "first"

    combined = combined.groupby("symbol", as_index=False).agg(aggregation)
    return normalize_universe(combined)


def _normalize_stock_history(
    raw: pd.DataFrame,
    symbol: str,
    *,
    provider: str,
) -> pd.DataFrame:
    if raw is None or len(raw) == 0:
        return pd.DataFrame()

    aliases = {
        "date": ("日期", "date"),
        "open": ("开盘", "open"),
        "close": ("收盘", "close"),
        "high": ("最高", "high"),
        "low": ("最低", "low"),
        "pct_chg": ("涨跌幅", "pct_chg"),
        "amount": ("成交额", "amount"),
        "turnover": ("换手率", "turnover"),
    }

    selected: dict[str, pd.Series] = {}
    for target, candidates in aliases.items():
        source = next((c for c in candidates if c in raw.columns), None)
        if source is None:
            if target in {"turnover", "pct_chg"}:
                continue
            raise ValueError(
                f"AKShare stock history for {symbol} is missing {target}; columns={list(raw.columns)}"
            )
        selected[target] = raw[source]

    out = pd.DataFrame(selected)
    out.insert(1, "symbol", normalize_symbol(symbol))
    out["date"] = pd.to_datetime(out["date"], errors="raise")
    for column in ("open", "close", "high", "low", "pct_chg", "amount", "turnover"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.sort_values("date").reset_index(drop=True)
    if "pct_chg" not in out.columns:
        out["pct_chg"] = out["close"].pct_change(fill_method=None) * 100.0

    out["board"] = infer_board(symbol)
    out["provider"] = provider
    return out


def _tencent_symbol(symbol: str) -> str:
    """Return an explicit market-prefixed symbol for AKShare's Tencent adapter.

    Explicit prefixes avoid provider-side market inference failures for less
    common code ranges such as 689-series STAR and 302-series ChiNext stocks.
    """
    code = normalize_symbol(symbol)
    if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return f"sh{code}"
    if code.startswith(("000", "001", "002", "003", "200", "300", "301", "302")):
        return f"sz{code}"
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return code


def fetch_stock_history(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    adjust: str = "",
    provider: str = "eastmoney",
    timeout_seconds: float = 15.0,
    client: Any | None = None,
) -> pd.DataFrame:
    """Fetch one A-share's daily history from an AKShare-backed provider."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    ak = _client(client)
    code = normalize_symbol(symbol)

    if provider == "eastmoney":
        raw = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust=adjust,
            timeout=timeout_seconds,
        )
    elif provider == "tencent":
        raw = ak.stock_zh_a_hist_tx(
            symbol=_tencent_symbol(code),
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            timeout=timeout_seconds,
        )
    else:
        raise ValueError(f"unsupported history provider: {provider}")

    return _normalize_stock_history(raw, code, provider=provider)


def download_universe_history(
    universe: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    adjust: str = "",
    providers: Sequence[str] = ("eastmoney", "tencent"),
    retries: int = 1,
    retry_backoff_seconds: float = 0.75,
    sleep_seconds: float = 0.0,
    timeout_seconds: float = 15.0,
    fail_fast: bool = False,
    client: Any | None = None,
) -> DownloadResult:
    """Download daily history with provider retry/fallback and diagnostics.

    Providers are attempted in order for each symbol. Eastmoney is kept first
    because it supplies an explicit daily percentage change; Tencent is a useful
    independent fallback and its percentage change is derived from consecutive
    closes after AKShare normalization. Each provider request is time-bounded so
    one remote socket cannot stall the complete point-in-time universe download.
    """
    normalized = normalize_universe(universe)
    ak = _client(client)
    price_frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []

    if retries < 0:
        raise ValueError("retries must be >= 0")
    if not providers:
        raise ValueError("at least one provider is required")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")

    for symbol in sorted(set(normalized["symbol"])):
        frame = pd.DataFrame()
        provider_errors: list[str] = []

        for provider in providers:
            for attempt in range(retries + 1):
                try:
                    frame = fetch_stock_history(
                        symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                        provider=provider,
                        timeout_seconds=timeout_seconds,
                        client=ak,
                    )
                    if not frame.empty:
                        break
                    provider_errors.append(f"{provider}[{attempt + 1}]: empty history")
                except Exception as exc:  # network/provider failures are data diagnostics
                    provider_errors.append(
                        f"{provider}[{attempt + 1}]: {type(exc).__name__}: {exc}"
                    )
                if attempt < retries and retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * (attempt + 1))
            if not frame.empty:
                break

        if frame.empty:
            message = " | ".join(provider_errors) if provider_errors else "no history"
            if fail_fast:
                raise RuntimeError(f"failed to download {symbol}: {message}")
            errors.append({"symbol": symbol, "error": message})
        else:
            price_frames.append(frame)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    prices = (
        pd.concat(price_frames, ignore_index=True)
        if price_frames
        else pd.DataFrame(
            columns=[
                "date",
                "symbol",
                "open",
                "close",
                "high",
                "low",
                "pct_chg",
                "amount",
                "board",
                "provider",
            ]
        )
    )
    error_df = pd.DataFrame(errors, columns=["symbol", "error"])
    return DownloadResult(prices=prices, errors=error_df)
