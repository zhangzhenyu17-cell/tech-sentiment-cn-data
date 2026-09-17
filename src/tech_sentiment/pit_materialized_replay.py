from __future__ import annotations

import pandas as pd

from .pit_public_materialization import validate_materialized_pit_records


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


def append_materialized_pit(
    existing: pd.DataFrame, new_rows: pd.DataFrame
) -> pd.DataFrame:
    """Append immutable PIT rows idempotently; historical rewrites fail closed."""

    if existing.empty:
        return validate_materialized_pit_records(new_rows)
    if new_rows.empty:
        return validate_materialized_pit_records(existing)
    left = validate_materialized_pit_records(existing)
    right = validate_materialized_pit_records(new_rows)
    columns = sorted(set(left.columns) | set(right.columns))
    by_id = {str(row["evidence_id"]): row for _, row in left.iterrows()}
    additions: list[pd.Series] = []
    for _, row in right.iterrows():
        key = str(row["evidence_id"])
        previous = by_id.get(key)
        if previous is None:
            additions.append(row)
            continue
        if any(_stable(previous.get(column)) != _stable(row.get(column)) for column in columns):
            raise ValueError(f"append-only PIT conflict for evidence_id={key}")
    if not additions:
        return left
    return validate_materialized_pit_records(
        pd.concat([left, pd.DataFrame(additions)], ignore_index=True, sort=False)
    )


def replay_materialized_pit_as_of(
    records: pd.DataFrame, market_date: object
) -> pd.DataFrame:
    """Return only records genuinely available by the requested market date."""

    out = validate_materialized_pit_records(records)
    date = pd.Timestamp(market_date).normalize()
    return out.loc[out["evidence_available_date"].le(date)].reset_index(drop=True)


def assert_prefix_replay_equality(
    records: pd.DataFrame, prefix_market_date: object
) -> None:
    """Prove later materialization cannot mutate an earlier as-of prefix."""

    out = validate_materialized_pit_records(records)
    date = pd.Timestamp(prefix_market_date).normalize()
    expected = out.loc[out["evidence_available_date"].le(date)].reset_index(drop=True)
    replayed = replay_materialized_pit_as_of(out, date)
    if list(expected.columns) != list(replayed.columns):
        raise ValueError("PIT prefix replay schema mismatch")
    if not expected.astype(str).equals(replayed.astype(str)):
        raise ValueError("later records changed an earlier PIT replay prefix")


__all__ = [
    "append_materialized_pit",
    "replay_materialized_pit_as_of",
    "assert_prefix_replay_equality",
]
