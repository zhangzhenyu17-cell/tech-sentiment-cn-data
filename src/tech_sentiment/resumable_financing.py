from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .capital_input_data import (
    SSE_MARGIN_SOURCE_ID,
    SSE_MARGIN_SOURCE_URL,
    SZSE_MARGIN_SOURCE_ID,
    SZSE_MARGIN_SOURCE_URL,
    qualify_financing_yuan,
)
from .financing_materialization import (
    FinancingMaterializationResult,
    materialize_financing_history,
)
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore


FINANCING_CHUNK_VERSION = "financing-monthly-checkpoint-v1"


@dataclass(frozen=True)
class ResumableFinancingResult:
    result: FinancingMaterializationResult
    resumed_chunks: int
    executed_chunks: int


def _month_groups(trading_dates: Iterable[object]) -> list[pd.DatetimeIndex]:
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


def _identity(source_commit: str, dates: pd.DatetimeIndex) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="bilateral-financing-history",
        producer_version=FINANCING_CHUNK_VERSION,
        source_commit=source_commit,
        source_identities=(SSE_MARGIN_SOURCE_ID, SZSE_MARGIN_SOURCE_ID),
        query_identity={
            "sse_raw_unit": "CNY",
            "szse_raw_unit": "CNY_100M",
            "canonical_unit": "CNY",
        },
        scope={
            "start_date": str(pd.Timestamp(dates.min()).date()),
            "end_date": str(pd.Timestamp(dates.max()).date()),
            "trading_dates": [str(pd.Timestamp(value).date()) for value in dates],
        },
    )


def _payload_hash(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def materialize_financing_monthly(
    *,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
    materialize_one: Callable[..., FinancingMaterializationResult] = materialize_financing_history,
) -> ResumableFinancingResult:
    store = ImmutableCheckpointStore(checkpoint_dir)
    raw_parts: list[pd.DataFrame] = []
    error_parts: list[pd.DataFrame] = []
    resumed = 0
    executed = 0
    all_dates: list[pd.Timestamp] = []

    deterministic_checkpoint_metadata = {
        "sse_source_identity": SSE_MARGIN_SOURCE_ID,
        "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
        "sse_raw_unit": "CNY",
        "szse_raw_unit": "CNY_100M",
        "canonical_unit": "CNY",
    }

    for dates in _month_groups(trading_dates):
        all_dates.extend(pd.Timestamp(value).normalize() for value in dates)
        identity = _identity(source_commit, dates)
        loaded = store.load(identity)
        if loaded is None:
            chunk = materialize_one(dates)
            store.save(
                identity,
                frames={
                    "raw": chunk.raw,
                    "canonical": chunk.canonical,
                    "errors": chunk.errors,
                },
                metadata=deterministic_checkpoint_metadata,
            )
            executed += 1
        else:
            chunk = FinancingMaterializationResult(
                raw=loaded.frames["raw"],
                canonical=loaded.frames["canonical"],
                errors=loaded.frames["errors"],
                summary=dict(loaded.receipt.get("metadata") or {}),
            )
            resumed += 1
        raw_parts.append(chunk.raw)
        error_parts.append(chunk.errors)

    raw = pd.concat(raw_parts, ignore_index=True, sort=False) if raw_parts else pd.DataFrame()
    if len(raw):
        raw["date"] = pd.to_datetime(raw["date"], errors="raise").dt.normalize()
        raw = raw.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    errors = (
        pd.concat([part for part in error_parts if len(part)], ignore_index=True, sort=False)
        if any(len(part) for part in error_parts)
        else pd.DataFrame(columns=["date", "exchange", "error"])
    )
    complete = raw.loc[raw["bilateral_complete"].astype(bool)].copy() if len(raw) else pd.DataFrame()
    canonical, qualification = qualify_financing_yuan(complete) if len(complete) else (
        pd.DataFrame(),
        {"state": "DATA_INSUFFICIENT", "n": 0},
    )
    dates = pd.DatetimeIndex(sorted(set(all_dates)))
    source_query = {
        "calendar_start": str(pd.Timestamp(dates.min()).date()),
        "calendar_end": str(pd.Timestamp(dates.max()).date()),
        "target_days": int(len(dates)),
        "sse_source_identity": SSE_MARGIN_SOURCE_ID,
        "sse_source_url": SSE_MARGIN_SOURCE_URL,
        "sse_raw_unit": "CNY",
        "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
        "szse_source_url": SZSE_MARGIN_SOURCE_URL,
        "szse_raw_unit": "CNY_100M",
        "canonical_unit": "CNY",
    }
    summary = {
        **source_query,
        "source_query_identity": _payload_hash(source_query),
        "bilateral_complete_days": int(raw["bilateral_complete"].astype(bool).sum()) if len(raw) else 0,
        "bilateral_coverage": float(raw["bilateral_complete"].astype(bool).mean()) if len(raw) else 0.0,
        "error_rows": int(len(errors)),
        "qualification_state": qualification.get("state", "DATA_INSUFFICIENT"),
        "median_scale_ratio": qualification.get("median_scale_ratio"),
        "role": "RESEARCH_INPUT",
        "included_in_capital_regime_composite": False,
        "no_unit_inference_from_anomaly": True,
        "checkpoint_schema": FINANCING_CHUNK_VERSION,
        "resumed_chunks": resumed,
        "executed_chunks": executed,
    }
    result = FinancingMaterializationResult(
        raw=raw,
        canonical=canonical,
        errors=errors,
        summary=summary,
    )
    return ResumableFinancingResult(
        result=result,
        resumed_chunks=resumed,
        executed_chunks=executed,
    )


__all__ = [
    "FINANCING_CHUNK_VERSION",
    "ResumableFinancingResult",
    "materialize_financing_monthly",
]
