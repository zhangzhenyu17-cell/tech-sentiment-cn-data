from __future__ import annotations

from typing import Mapping

import pandas as pd


# Runtime observations describe how a run happened, not what public evidence was
# materialized. They must never affect canonical managed files or bundle hashes.
_RUNTIME_EXACT_KEYS = {"captured_at_utc", "generated_at_utc"}
_RUNTIME_PREFIXES = (
    "resumed_",
    "executed_",
    "checkpoint_resumed_",
    "checkpoint_executed_",
)


def is_runtime_metadata_key(key: object) -> bool:
    text = str(key)
    return text in _RUNTIME_EXACT_KEYS or text.startswith(_RUNTIME_PREFIXES)


def canonicalize_metadata(value: object) -> object:
    """Recursively remove execution-path metadata from a canonical payload."""

    if isinstance(value, Mapping):
        return {
            str(key): canonicalize_metadata(item)
            for key, item in value.items()
            if not is_runtime_metadata_key(key)
        }
    if isinstance(value, list):
        return [canonicalize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [canonicalize_metadata(item) for item in value]
    return value


def canonicalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove non-evidence runtime columns while preserving row/value semantics."""

    if frame is None:
        return pd.DataFrame()
    out = frame.copy()
    runtime_columns = [column for column in out.columns if is_runtime_metadata_key(column)]
    if runtime_columns:
        out = out.drop(columns=runtime_columns)
    return out


__all__ = [
    "is_runtime_metadata_key",
    "canonicalize_metadata",
    "canonicalize_frame",
]
