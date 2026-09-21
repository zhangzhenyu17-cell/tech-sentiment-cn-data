from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .capital_input_data import (
    EtfShareFetchResult,
    ExchangeTurnoverFetchResult,
    SSE_ETF_SHARE_SOURCE_ID,
    SSE_TURNOVER_SOURCE_ID,
    SZSE_TURNOVER_SOURCE_ID,
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
)
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .v4c03_szse_etf_shares import (
    SZSE_ETF_SHARE_SOURCE_ID,
    SzseEtfShareFetchResult,
    fetch_szse_etf_share_history,
)


CAPITAL_CHUNK_VERSION = "capital-monthly-checkpoint-v1"


@dataclass(frozen=True)
class ResumableCapitalResult:
    etf: EtfShareFetchResult
    turnover: ExchangeTurnoverFetchResult
    resumed_chunks: int
    executed_chunks: int


def month_groups(trading_dates: Iterable[object]) -> list[pd.DatetimeIndex]:
    dates = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(dates):
        raise ValueError("trading_dates cannot be empty")
    series = pd.Series(dates, index=dates)
    return [pd.DatetimeIndex(part.values) for _, part in series.groupby(dates.to_period("M"))]


def checkpointcheckpoint_identity(
    *,
    producer: str,
    source_revision: str,
    source_identities: tuple[str, ...],
    dates: pd.DatetimeIndex,
    query_identity: dict[str, object],
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer=producer,
        producer_version=CAPITAL_CHUNK_VERSION,
        source_commit=source_revision,
        source_identities=source_identities,
        query_identity=query_identity,
        scope={
            "start_date": str(pd.Timestamp(dates.min()).date()),
            "end_date": str(pd.Timestamp(dates.max()).date()),
            "trading_dates": [str(pd.Timestamp(value).date()) for value in dates],
        },
    )


def _concat(parts: list[pd.DataFrame], *, sort: tuple[str, ...]) -> pd.DataFrame:
    nonempty = [part for part in parts if part is not None and len(part)]
    if not nonempty:
        if parts:
            return parts[0].iloc[0:0].copy()
        return pd.DataFrame()
    out = pd.concat(nonempty, ignore_index=True, sort=False)
    for column in sort:
        if column in out.columns and column == "date":
            out[column] = pd.to_datetime(out[column], errors="raise").dt.normalize()
    available_sort = [column for column in sort if column in out.columns]
    return out.sort_values(available_sort).reset_index(drop=True) if available_sort else out.reset_index(drop=True)


def _normalize_etf_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    if "evidence_available_date" in out.columns:
        out["evidence_available_date"] = pd.to_datetime(
            out["evidence_available_date"], errors="raise"
        ).dt.normalize()
    if "fund_code" in out.columns:
        out["fund_code"] = out["fund_code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return out


def materialize_capital_monthly(
    *,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    source_commit: str,
    checkpoint_dir: str | Path,
    checkpoint_revision: str | None = None,
    capture_date: str | None = None,
    sleep_seconds: float = 0.05,
    etf_history_fetcher: Callable[..., EtfShareFetchResult] = fetch_sse_etf_share_history,
    turnover_history_fetcher: Callable[..., ExchangeTurnoverFetchResult] = fetch_sse_szse_a_share_turnover_history,
) -> ResumableCapitalResult:
    codes = tuple(sorted({str(code).zfill(6) for code in fund_codes}))
    if not codes:
        raise ValueError("at least one fund code is required")
    store = ImmutableCheckpointStore(checkpoint_dir)
    etf_data_parts: list[pd.DataFrame] = []
    etf_error_parts: list[pd.DataFrame] = []
    sse_parts: list[pd.DataFrame] = []
    szse_parts: list[pd.DataFrame] = []
    combined_parts: list[pd.DataFrame] = []
    turnover_error_parts: list[pd.DataFrame] = []
    resumed = 0
    executed = 0

    for dates in month_groups(trading_dates):
        etf_identity = checkpoint_identity(
            producer="sse-etf-share-history",
            source_revision=checkpoint_revision or source_commit,
            source_identities=(SSE_ETF_SHARE_SOURCE_ID,),
            dates=dates,
            query_identity={
                "fund_codes": list(codes),
                "capture_date": capture_date,
            },
        )
        loaded_etf = store.load(etf_identity)
        if loaded_etf is None:
            etf_result = etf_history_fetcher(
                trading_dates=dates,
                fund_codes=codes,
                sleep_seconds=sleep_seconds,
            )
            store.save(
                etf_identity,
                frames={"data": etf_result.data, "errors": etf_result.errors},
                metadata={
                    "fund_codes": list(codes),
                    "actual_source_commit": source_commit,
                    "checkpoint_revision": checkpoint_revision or source_commit,
                    "capture_date": capture_date,
                    "permanent_reuse_eligible": bool(
                        etf_result.errors.empty
                        or (
                            "error" in etf_result.errors.columns
                            and etf_result.errors["error"].astype(str)
                            .eq("NO_MATCHING_ETF_ROW").all()
                        )
                    ),
                },
            )
            executed += 1
        else:
            etf_result = EtfShareFetchResult(
                data=loaded_etf.frames["data"],
                errors=loaded_etf.frames["errors"],
            )
            resumed += 1
        etf_data_parts.append(_normalize_etf_frame(etf_result.data))
        etf_error_parts.append(etf_result.errors)

        turnover_identity = checkpoint_identity(
            producer="sse-szse-a-share-turnover-history",
            source_revision=checkpoint_revision or source_commit,
            source_identities=(SSE_TURNOVER_SOURCE_ID, SZSE_TURNOVER_SOURCE_ID),
            dates=dates,
            query_identity={
                "scope": "SSE_SZSE_A_SHARES",
                "capture_date": capture_date,
            },
        )
        loaded_turnover = store.load(turnover_identity)
        if loaded_turnover is None:
            turnover_result = turnover_history_fetcher(
                trading_dates=dates,
                sleep_seconds=sleep_seconds,
            )
            store.save(
                turnover_identity,
                frames={
                    "sse": turnover_result.sse,
                    "szse": turnover_result.szse,
                    "combined": turnover_result.combined,
                    "errors": turnover_result.errors,
                },
                metadata={
                    "scope": "SSE_SZSE_A_SHARES",
                    "actual_source_commit": source_commit,
                    "checkpoint_revision": checkpoint_revision or source_commit,
                    "capture_date": capture_date,
                    "permanent_reuse_eligible": bool(turnover_result.errors.empty),
                },
            )
            executed += 1
        else:
            turnover_result = ExchangeTurnoverFetchResult(
                sse=loaded_turnover.frames["sse"],
                szse=loaded_turnover.frames["szse"],
                combined=loaded_turnover.frames["combined"],
                errors=loaded_turnover.frames["errors"],
            )
            resumed += 1
        sse_parts.append(turnover_result.sse)
        szse_parts.append(turnover_result.szse)
        combined_parts.append(turnover_result.combined)
        turnover_error_parts.append(turnover_result.errors)

    etf = EtfShareFetchResult(
        data=_normalize_etf_frame(_concat(etf_data_parts, sort=("date", "fund_code"))),
        errors=_concat(etf_error_parts, sort=("date",)),
    )
    turnover = ExchangeTurnoverFetchResult(
        sse=_concat(sse_parts, sort=("date",)),
        szse=_concat(szse_parts, sort=("date",)),
        combined=_concat(combined_parts, sort=("date",)),
        errors=_concat(turnover_error_parts, sort=("date", "exchange")),
    )
    return ResumableCapitalResult(
        etf=etf,
        turnover=turnover,
        resumed_chunks=resumed,
        executed_chunks=executed,
    )




@dataclass(frozen=True)
class ResumableSzseEtfResult:
    result: SzseEtfShareFetchResult
    resumed_chunks: int
    executed_chunks: int


def materialize_szse_etf_monthly(
    *,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    source_commit: str,
    checkpoint_dir: str | Path,
    checkpoint_revision: str | None = None,
    capture_date: str | None = None,
    sleep_seconds: float = 0.05,
    fetcher: Callable[..., SzseEtfShareFetchResult] = fetch_szse_etf_share_history,
) -> ResumableSzseEtfResult:
    codes = tuple(sorted({str(code).zfill(6) for code in fund_codes}))
    if not codes:
        raise ValueError("at least one fund code is required")
    store = ImmutableCheckpointStore(checkpoint_dir)
    data_parts: list[pd.DataFrame] = []
    error_parts: list[pd.DataFrame] = []
    resumed = 0
    executed = 0

    for dates in month_groups(trading_dates):
        identity = checkpoint_identity(
            producer="szse-etf-share-history",
            source_revision=checkpoint_revision or source_commit,
            source_identities=(SZSE_ETF_SHARE_SOURCE_ID,),
            dates=dates,
            query_identity={
                "fund_codes": list(codes),
                "capture_date": capture_date,
            },
        )
        loaded = store.load(identity)
        if loaded is None:
            result = fetcher(
                start_date=str(pd.Timestamp(dates.min()).date()),
                end_date=str(pd.Timestamp(dates.max()).date()),
                trading_dates=dates,
                fund_codes=codes,
                sleep_seconds=sleep_seconds,
            )
            store.save(
                identity,
                frames={"data": result.data, "errors": result.errors},
                metadata={
                    "fund_codes": list(codes),
                    "actual_source_commit": source_commit,
                    "checkpoint_revision": checkpoint_revision or source_commit,
                    "capture_date": capture_date,
                    "permanent_reuse_eligible": bool(result.errors.empty),
                },
            )
            executed += 1
        else:
            result = SzseEtfShareFetchResult(
                data=loaded.frames["data"],
                errors=loaded.frames["errors"],
            )
            resumed += 1
        data_parts.append(_normalize_etf_frame(result.data))
        error_parts.append(result.errors)

    combined = SzseEtfShareFetchResult(
        data=_normalize_etf_frame(_concat(data_parts, sort=("date", "fund_code"))),
        errors=_concat(error_parts, sort=("chunk_start", "chunk_end")),
    )
    return ResumableSzseEtfResult(
        result=combined,
        resumed_chunks=resumed,
        executed_chunks=executed,
    )


def expected_capital_checkpoint_identities(
    *,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    checkpoint_revision: str,
    capture_date: str,
) -> list[tuple[str, CheckpointIdentity]]:
    codes = tuple(sorted({str(code).zfill(6) for code in fund_codes}))
    rows: list[tuple[str, CheckpointIdentity]] = []
    for dates in month_groups(trading_dates):
        rows.append(
            (
                "sse",
                checkpoint_identity(
                    producer="sse-etf-share-history",
                    source_revision=checkpoint_revision,
                    source_identities=(SSE_ETF_SHARE_SOURCE_ID,),
                    dates=dates,
                    query_identity={
                        "fund_codes": list(codes),
                        "capture_date": capture_date,
                    },
                ),
            )
        )
        rows.append(
            (
                "sse",
                checkpoint_identity(
                    producer="sse-szse-a-share-turnover-history",
                    source_revision=checkpoint_revision,
                    source_identities=(
                        SSE_TURNOVER_SOURCE_ID,
                        SZSE_TURNOVER_SOURCE_ID,
                    ),
                    dates=dates,
                    query_identity={
                        "scope": "SSE_SZSE_A_SHARES",
                        "capture_date": capture_date,
                    },
                ),
            )
        )
    return rows


def expected_szse_etf_checkpoint_identities(
    *,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    checkpoint_revision: str,
    capture_date: str,
) -> list[tuple[str, CheckpointIdentity]]:
    codes = tuple(sorted({str(code).zfill(6) for code in fund_codes}))
    rows: list[tuple[str, CheckpointIdentity]] = []
    for dates in month_groups(trading_dates):
        rows.append(
            (
                "szse",
                checkpoint_identity(
                    producer="szse-etf-share-history",
                    source_revision=checkpoint_revision,
                    source_identities=(SZSE_ETF_SHARE_SOURCE_ID,),
                    dates=dates,
                    query_identity={
                        "fund_codes": list(codes),
                        "capture_date": capture_date,
                    },
                ),
            )
        )
    return rows


__all__ = [
    "CAPITAL_CHUNK_VERSION",
    "ResumableCapitalResult",
    "ResumableSzseEtfResult",
    "month_groups",
    "checkpoint_identity",
    "materialize_capital_monthly",
    "materialize_szse_etf_monthly",
    "expected_capital_checkpoint_identities",
    "expected_szse_etf_checkpoint_identities",
]
