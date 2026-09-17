from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd


def _normalize_key_columns(frame: pd.DataFrame, key_columns: Iterable[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in key_columns:
        if column not in out.columns:
            raise ValueError(f"checkpoint missing key column: {column}")
        if column == "date" or column.endswith("_date"):
            out[column] = pd.to_datetime(out[column], errors="raise").dt.strftime("%Y-%m-%d")
        else:
            out[column] = out[column].astype(str)
    return out


def validate_checkpoint_frame(
    frame: pd.DataFrame,
    *,
    required_columns: Iterable[str],
    key_columns: Iterable[str],
    expected_constants: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    required = list(required_columns)
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"checkpoint missing required columns: {sorted(missing)}")
    out = _normalize_key_columns(frame, key_columns)
    if out.duplicated(list(key_columns)).any():
        raise ValueError("checkpoint contains duplicate identities")
    if expected_constants:
        for column, expected in expected_constants.items():
            if column not in out.columns:
                raise ValueError(f"checkpoint missing constant column: {column}")
            values = out[column].dropna().astype(str)
            if len(values) and not values.eq(str(expected)).all():
                raise ValueError(
                    f"checkpoint constant mismatch for {column}: expected {expected}"
                )
    return out.reset_index(drop=True)


def load_csv_checkpoint(
    path: str | Path,
    *,
    required_columns: Iterable[str],
    key_columns: Iterable[str],
    expected_constants: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    checkpoint = Path(path)
    if not checkpoint.is_file():
        return pd.DataFrame(columns=list(required_columns))
    frame = pd.read_csv(checkpoint, dtype=str)
    return validate_checkpoint_frame(
        frame,
        required_columns=required_columns,
        key_columns=key_columns,
        expected_constants=expected_constants,
    )


def merge_csv_checkpoint(
    path: str | Path,
    incoming: pd.DataFrame,
    *,
    required_columns: Iterable[str],
    key_columns: Iterable[str],
    expected_constants: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    checkpoint = Path(path)
    existing = load_csv_checkpoint(
        checkpoint,
        required_columns=required_columns,
        key_columns=key_columns,
        expected_constants=expected_constants,
    )
    if incoming is None or incoming.empty:
        return existing
    new_rows = validate_checkpoint_frame(
        incoming,
        required_columns=required_columns,
        key_columns=key_columns,
        expected_constants=expected_constants,
    )
    combined = pd.concat([existing, new_rows], ignore_index=True, sort=False)
    combined = _normalize_key_columns(combined, key_columns)
    if combined.duplicated(list(key_columns)).any():
        raise ValueError("checkpoint append would create duplicate identities")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temp = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
    combined.to_csv(temp, index=False)
    temp.replace(checkpoint)
    return combined.reset_index(drop=True)


__all__ = [
    "load_csv_checkpoint",
    "merge_csv_checkpoint",
    "validate_checkpoint_frame",
]
