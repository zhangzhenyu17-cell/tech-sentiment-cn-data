from __future__ import annotations

from typing import Iterable

import pandas as pd


REQUIRED_PIT_LEDGER_COLUMNS = (
    "evidence_id",
    "entity_id",
    "evidence_type",
    "event_date",
    "evidence_available_date",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "provenance",
    "ingestion_identity",
    "availability_state",
)


def _stable(value: object) -> str:
    if value is None:
        return "<NA>"
    try:
        if pd.isna(value):
            return "<NA>"
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def validate_pit_ledger(records: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_PIT_LEDGER_COLUMNS if column not in records.columns]
    if missing:
        raise ValueError(f"PIT ledger missing required columns: {missing}")
    out = records.copy().reset_index(drop=True)
    out["event_date"] = pd.to_datetime(out["event_date"], errors="raise").dt.normalize()
    out["evidence_available_date"] = pd.to_datetime(
        out["evidence_available_date"], errors="raise"
    ).dt.normalize()
    if (out["evidence_available_date"] < out["event_date"]).any():
        raise ValueError("PIT ledger contains future-leakage date inversion")
    for column in (
        "evidence_id",
        "entity_id",
        "evidence_type",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "ingestion_identity",
        "availability_state",
    ):
        if out[column].map(lambda value: bool(_stable(value).strip()) and _stable(value) != "<NA>").eq(False).any():
            raise ValueError(f"PIT ledger requires non-empty {column}")
    if out["evidence_id"].duplicated().any():
        duplicated = sorted(out.loc[out["evidence_id"].duplicated(keep=False), "evidence_id"].astype(str).unique())
        raise ValueError(f"duplicate evidence_id in PIT ledger: {duplicated[:5]}")
    return out.sort_values(
        ["evidence_available_date", "event_date", "entity_id", "document_id", "revision_id"]
    ).reset_index(drop=True)


def append_only_pit(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    """Idempotently append immutable evidence; conflicting rewrites fail closed."""
    if existing.empty:
        return validate_pit_ledger(new_rows)
    if new_rows.empty:
        return validate_pit_ledger(existing)
    left = validate_pit_ledger(existing)
    right = validate_pit_ledger(new_rows)
    by_id = {str(row["evidence_id"]): row for _, row in left.iterrows()}
    columns = sorted(set(left.columns) | set(right.columns))
    append_rows: list[pd.Series] = []
    for _, row in right.iterrows():
        key = str(row["evidence_id"])
        previous = by_id.get(key)
        if previous is None:
            append_rows.append(row)
            continue
        if any(_stable(previous.get(column)) != _stable(row.get(column)) for column in columns):
            raise ValueError(f"append-only PIT conflict for evidence_id={key}")
    if not append_rows:
        return left
    combined = pd.concat([left, pd.DataFrame(append_rows)], ignore_index=True, sort=False)
    return validate_pit_ledger(combined)


def replay_pit_as_of(
    records: pd.DataFrame,
    market_date: object,
    *,
    entity_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Replay exactly the evidence knowable by a market date."""
    out = validate_pit_ledger(records)
    date = pd.Timestamp(market_date).normalize()
    mask = out["evidence_available_date"].le(date)
    if entity_ids is not None:
        allowed = {str(value) for value in entity_ids}
        mask &= out["entity_id"].astype(str).isin(allowed)
    return out.loc[mask].reset_index(drop=True)


def assert_prefix_replay_equality(
    records: pd.DataFrame,
    prefix_market_date: object,
) -> None:
    """Ensure later records cannot mutate an earlier replay prefix."""
    full = validate_pit_ledger(records)
    prefix_date = pd.Timestamp(prefix_market_date).normalize()
    replay = replay_pit_as_of(full, prefix_date)
    expected = full.loc[full["evidence_available_date"].le(prefix_date)].reset_index(drop=True)
    if list(replay.columns) != list(expected.columns) or len(replay) != len(expected):
        raise ValueError("PIT prefix replay mismatch")
    if not replay.astype(str).equals(expected.astype(str)):
        raise ValueError("later evidence changed an earlier PIT replay prefix")


__all__ = [
    "REQUIRED_PIT_LEDGER_COLUMNS",
    "validate_pit_ledger",
    "append_only_pit",
    "replay_pit_as_of",
    "assert_prefix_replay_equality",
]
