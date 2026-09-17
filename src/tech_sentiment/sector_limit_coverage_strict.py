from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

import pandas as pd

from .universe import normalize_symbol, normalize_universe, universe_diagnostics


@dataclass(frozen=True)
class StrictLimitCoverageAudit:
    eligible: bool
    required_daily_coverage: float
    expected_member_days: int
    observed_member_days: int
    eligible_member_days: int
    missing_member_days: int
    minimum_daily_coverage: float
    minimum_daily_observation_coverage: float
    days_below_threshold: int
    errors: tuple[str, ...]
    daily_coverage: pd.DataFrame


def _bool_series(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    if values.dtype == bool:
        return values.astype(bool), pd.Series(False, index=values.index)
    normalised = values.astype(str).str.strip().str.lower()
    mapping = {
        "true": True,
        "false": False,
        "1": True,
        "0": False,
        "yes": True,
        "no": False,
    }
    mapped = normalised.map(mapping)
    invalid = mapped.isna()
    return mapped.fillna(False).astype(bool), invalid


def build_expected_member_days(
    universe: pd.DataFrame,
    trading_dates: Iterable[object],
) -> pd.DataFrame:
    """Expand a point-in-time universe onto a supplied market trading calendar.

    The expected matrix is independent of provider row availability. A missing
    provider row therefore remains in the denominator and cannot silently improve
    coverage.
    """

    diagnostics = universe_diagnostics(universe)
    if not diagnostics.has_point_in_time_history:
        raise ValueError("universe is not genuine point-in-time history")

    uni = normalize_universe(universe)
    if "effective_start" not in uni.columns or "effective_end" not in uni.columns:
        raise ValueError("point-in-time universe requires effective_start/effective_end")
    if uni["effective_start"].isna().any() or uni["effective_end"].isna().any():
        raise ValueError("strict member-day expansion requires bounded effective ranges")

    dates = pd.Series(pd.to_datetime(list(trading_dates), errors="raise")).dt.normalize()
    dates = pd.DatetimeIndex(sorted(set(dates.tolist())))
    if dates.empty:
        return pd.DataFrame(columns=["date", "symbol"])

    parts: list[pd.DataFrame] = []
    for row in uni.itertuples(index=False):
        start = pd.Timestamp(row.effective_start).normalize()
        end = pd.Timestamp(row.effective_end).normalize()
        if end < start:
            raise ValueError(f"invalid membership range for {row.symbol}: {start} > {end}")
        active_dates = dates[(dates >= start) & (dates <= end)]
        if active_dates.empty:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "date": active_dates,
                    "symbol": normalize_symbol(row.symbol),
                }
            )
        )

    if not parts:
        return pd.DataFrame(columns=["date", "symbol"])
    expected = pd.concat(parts, ignore_index=True)
    if expected.duplicated(subset=["date", "symbol"], keep=False).any():
        raise ValueError("overlapping point-in-time membership ranges create duplicate member-days")
    return expected.sort_values(["date", "symbol"]).reset_index(drop=True)


def audit_strict_member_day_limit_coverage(
    rows: pd.DataFrame,
    universe: pd.DataFrame,
    trading_dates: Iterable[object],
    *,
    min_daily_coverage: float = 0.95,
) -> StrictLimitCoverageAudit:
    """Audit limit-rule coverage against every expected point-in-time member-day."""

    if not 0.0 <= min_daily_coverage <= 1.0:
        raise ValueError("min_daily_coverage must be between 0 and 1")

    expected = build_expected_member_days(universe, trading_dates)
    errors: list[str] = []
    if expected.empty:
        return StrictLimitCoverageAudit(
            eligible=False,
            required_daily_coverage=min_daily_coverage,
            expected_member_days=0,
            observed_member_days=0,
            eligible_member_days=0,
            missing_member_days=0,
            minimum_daily_coverage=0.0,
            minimum_daily_observation_coverage=0.0,
            days_below_threshold=0,
            errors=("no expected member-days",),
            daily_coverage=pd.DataFrame(),
        )

    required = {"date", "symbol", "limit_eligible", "limit_rule_source"}
    missing_columns = required - set(rows.columns)
    if missing_columns:
        return StrictLimitCoverageAudit(
            eligible=False,
            required_daily_coverage=min_daily_coverage,
            expected_member_days=len(expected),
            observed_member_days=0,
            eligible_member_days=0,
            missing_member_days=len(expected),
            minimum_daily_coverage=0.0,
            minimum_daily_observation_coverage=0.0,
            days_below_threshold=int(expected["date"].nunique()),
            errors=(f"rows missing columns: {sorted(missing_columns)}",),
            daily_coverage=pd.DataFrame(),
        )

    actual = rows.copy()
    actual["date"] = pd.to_datetime(actual["date"], errors="raise").dt.normalize()
    actual["symbol"] = actual["symbol"].map(normalize_symbol)
    duplicate = actual.duplicated(subset=["date", "symbol"], keep=False)
    if duplicate.any():
        errors.append("duplicate provider stock-day rows")
        actual = actual.drop_duplicates(subset=["date", "symbol"], keep="last")

    eligible, bad_boolean = _bool_series(actual["limit_eligible"])
    actual = actual.assign(_limit_eligible=eligible, _bad_boolean=bad_boolean)
    source = actual["limit_rule_source"].fillna("").astype(str).str.strip()
    actual["_valid_source"] = source.ne("")

    merged = expected.merge(
        actual[["date", "symbol", "_limit_eligible", "_bad_boolean", "_valid_source"]],
        on=["date", "symbol"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    merged["observed"] = merged["_merge"].eq("both")
    merged["eligible"] = (
        merged["observed"]
        & merged["_limit_eligible"].fillna(False).astype(bool)
        & ~merged["_bad_boolean"].fillna(False).astype(bool)
        & merged["_valid_source"].fillna(False).astype(bool)
    )

    invalid_observed = merged["observed"] & (
        merged["_bad_boolean"].fillna(False).astype(bool)
        | ~merged["_valid_source"].fillna(False).astype(bool)
    )
    if invalid_observed.any():
        errors.append(f"invalid observed member-day rows: {int(invalid_observed.sum())}")

    daily = merged.groupby("date", as_index=False).agg(
        expected_member_rows=("symbol", "size"),
        observed_member_rows=("observed", "sum"),
        eligible_member_rows=("eligible", "sum"),
    )
    daily["observation_coverage"] = daily["observed_member_rows"] / daily["expected_member_rows"]
    daily["limit_coverage"] = daily["eligible_member_rows"] / daily["expected_member_rows"]
    daily = daily.sort_values("date").reset_index(drop=True)

    minimum = float(daily["limit_coverage"].min())
    min_observation = float(daily["observation_coverage"].min())
    days_below = int((daily["limit_coverage"] < min_daily_coverage).sum())
    if days_below:
        errors.append(
            f"{days_below} trading days below strict member-day coverage {min_daily_coverage:.3f}"
        )

    expected_count = int(len(merged))
    observed_count = int(merged["observed"].sum())
    eligible_count = int(merged["eligible"].sum())
    return StrictLimitCoverageAudit(
        eligible=not errors,
        required_daily_coverage=min_daily_coverage,
        expected_member_days=expected_count,
        observed_member_days=observed_count,
        eligible_member_days=eligible_count,
        missing_member_days=expected_count - observed_count,
        minimum_daily_coverage=minimum,
        minimum_daily_observation_coverage=min_observation,
        days_below_threshold=days_below,
        errors=tuple(errors),
        daily_coverage=daily,
    )
