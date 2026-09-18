from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pandas as pd

from .data_akshare import fetch_stock_history
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore


PRICE_MATERIALIZER_VERSION = "pit-stock-close-symbol-year-v1"
PRICE_SOURCE_ID = "PUBLIC_A_SHARE_DAILY_CLOSE"


@dataclass(frozen=True)
class PitPriceMaterializationResult:
    prices: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _year_chunks(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    chunks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for year in range(start.year, end.year + 1):
        left = max(start, pd.Timestamp(year=year, month=1, day=1))
        right = min(end, pd.Timestamp(year=year, month=12, day=31))
        chunks.append((left, right))
    return chunks


def _checkpoint_identity(
    *,
    source_commit: str,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    providers: Sequence[str],
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="pit-stock-close",
        producer_version=PRICE_MATERIALIZER_VERSION,
        source_commit=source_commit,
        source_identities=(PRICE_SOURCE_ID,),
        query_identity={"symbol": symbol, "providers": list(providers), "adjust": ""},
        scope={"start_date": str(start.date()), "end_date": str(end.date())},
    )


def materialize_pit_stock_prices(
    symbols: Iterable[str],
    *,
    start_date: object,
    end_date: object,
    source_commit: str,
    checkpoint_dir: str | Path,
    providers: Sequence[str] = ("tencent", "eastmoney"),
    fetcher: Callable[..., pd.DataFrame] = fetch_stock_history,
) -> PitPriceMaterializationResult:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    if not providers:
        raise ValueError("at least one provider is required")
    unique_symbols = sorted({str(value).zfill(6) for value in symbols})
    if not unique_symbols:
        raise ValueError("at least one symbol is required")
    store = ImmutableCheckpointStore(checkpoint_dir)

    price_parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    resumed_chunks = 0
    executed_chunks = 0
    for symbol in unique_symbols:
        for chunk_start, chunk_end in _year_chunks(start, end):
            identity = _checkpoint_identity(
                source_commit=source_commit,
                symbol=symbol,
                start=chunk_start,
                end=chunk_end,
                providers=providers,
            )
            loaded = store.load(identity)
            if loaded is not None:
                frame = loaded.frames["prices"]
                resumed_chunks += 1
                status = "COMPLETE_WINDOW"
                provider_used = str(loaded.receipt.get("metadata", {}).get("provider_used") or "")
            else:
                frame = pd.DataFrame()
                provider_used = ""
                provider_errors: list[str] = []
                for provider in providers:
                    try:
                        candidate = fetcher(
                            symbol,
                            start_date=str(chunk_start.date()),
                            end_date=str(chunk_end.date()),
                            adjust="",
                            provider=provider,
                        )
                        if candidate is not None and len(candidate):
                            frame = candidate.copy()
                            provider_used = provider
                            break
                        provider_errors.append(f"{provider}:empty")
                    except Exception as exc:
                        provider_errors.append(f"{provider}:{type(exc).__name__}:{exc}")
                if frame.empty:
                    status = "FAILED"
                    errors.append(
                        {
                            "symbol": symbol,
                            "chunk_start": str(chunk_start.date()),
                            "chunk_end": str(chunk_end.date()),
                            "error": " | ".join(provider_errors) or "NO_HISTORY",
                        }
                    )
                else:
                    status = "COMPLETE_WINDOW"
                    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
                    frame = frame[
                        frame["date"].between(chunk_start, chunk_end)
                    ].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
                    if "provider" not in frame.columns:
                        frame["provider"] = provider_used
                    store.save(
                        identity,
                        frames={"prices": frame},
                        metadata={"provider_used": provider_used},
                    )
                    executed_chunks += 1
            if len(frame):
                if "symbol" not in frame.columns:
                    frame["symbol"] = symbol
                frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
                price_parts.append(frame)
            coverage_rows.append(
                {
                    "source_identity": PRICE_SOURCE_ID,
                    "symbol": symbol,
                    "coverage_start": chunk_start,
                    "coverage_end": chunk_end,
                    "query_status": status,
                    "provider_used": provider_used,
                    "rows": int(len(frame)),
                }
            )

    prices = pd.concat(price_parts, ignore_index=True, sort=False) if price_parts else pd.DataFrame()
    if len(prices):
        prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
        prices = prices.sort_values(["symbol", "date"]).drop_duplicates(
            ["symbol", "date"], keep="last"
        ).reset_index(drop=True)
    coverage = pd.DataFrame(coverage_rows)
    complete_chunks = int(coverage["query_status"].eq("COMPLETE_WINDOW").sum()) if len(coverage) else 0
    summary = {
        "source_identity": PRICE_SOURCE_ID,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "symbols": len(unique_symbols),
        "chunks": int(len(coverage)),
        "complete_chunks": complete_chunks,
        "resumed_chunks": resumed_chunks,
        "executed_chunks": executed_chunks,
        "readiness_state": (
            "QUALIFIED_INPUT" if len(coverage) and complete_chunks == len(coverage) else "PARTIAL_COVERAGE" if complete_chunks else "DATA_INSUFFICIENT"
        ),
        "adjustment": "NONE_UNADJUSTED_CLOSE",
        "no_forward_fill": True,
    }
    return PitPriceMaterializationResult(
        prices=prices,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["symbol", "chunk_start", "chunk_end", "error"]),
        summary=summary,
    )


__all__ = [
    "PRICE_MATERIALIZER_VERSION",
    "PRICE_SOURCE_ID",
    "PitPriceMaterializationResult",
    "materialize_pit_stock_prices",
]
