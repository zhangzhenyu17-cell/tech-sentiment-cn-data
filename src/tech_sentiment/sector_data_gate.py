from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

import pandas as pd

from .universe import apply_universe_membership, universe_diagnostics


@dataclass(frozen=True)
class MembershipEvidenceAudit:
    eligible: bool
    expected_periods: int
    complete_periods: int
    pending_periods: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class LimitRuleCoverageAudit:
    eligible: bool
    minimum_daily_coverage: float
    required_daily_coverage: float
    days_below_threshold: int
    invalid_rows: int
    errors: tuple[str, ...]
    daily_coverage: pd.DataFrame


def _normalise_period(value: object) -> str:
    text = str(value).strip()
    try:
        period = pd.Period(text, freq="M")
    except Exception as exc:
        raise ValueError(f"invalid expected_period {value!r}; expected YYYY-MM") from exc
    return str(period)


def audit_membership_evidence(
    manifest: pd.DataFrame,
    *,
    expected_periods: Iterable[str],
) -> MembershipEvidenceAudit:
    """Audit scheduled-rebalance evidence without looking at market outcomes.

    The manifest is deliberately fail-closed.  A scheduled period is complete
    only when it has an exact effective date, an admissible evidence type and a
    durable source URL.  Missing search results are never interpreted as
    evidence of a no-change rebalance.
    """

    required = {
        "expected_period",
        "effective_date",
        "change_status",
        "evidence_type",
        "source_url",
        "evidence_status",
    }
    missing_columns = required - set(manifest.columns)
    expected = tuple(_normalise_period(value) for value in expected_periods)
    errors: list[str] = []

    if missing_columns:
        return MembershipEvidenceAudit(
            eligible=False,
            expected_periods=len(expected),
            complete_periods=0,
            pending_periods=expected,
            errors=(f"missing columns: {sorted(missing_columns)}",),
        )

    frame = manifest.copy()
    try:
        frame["expected_period"] = frame["expected_period"].map(_normalise_period)
    except ValueError as exc:
        return MembershipEvidenceAudit(
            eligible=False,
            expected_periods=len(expected),
            complete_periods=0,
            pending_periods=expected,
            errors=(str(exc),),
        )

    duplicate_periods = sorted(
        frame.loc[frame["expected_period"].duplicated(keep=False), "expected_period"].unique()
    )
    if duplicate_periods:
        errors.append(f"duplicate expected periods: {duplicate_periods}")

    expected_set = set(expected)
    present_set = set(frame["expected_period"])
    missing_periods = sorted(expected_set - present_set)
    if missing_periods:
        errors.append(f"missing expected periods: {missing_periods}")

    allowed_status = {"complete", "pending"}
    invalid_status = sorted(set(frame["evidence_status"].astype(str)) - allowed_status)
    if invalid_status:
        errors.append(f"invalid evidence_status values: {invalid_status}")

    allowed_change_status = {"changed", "no_change"}
    complete_mask = frame["evidence_status"].astype(str).eq("complete")
    complete = frame[complete_mask].copy()

    if len(complete):
        invalid_change = sorted(
            set(complete["change_status"].astype(str)) - allowed_change_status
        )
        if invalid_change:
            errors.append(f"invalid complete change_status values: {invalid_change}")

        effective = pd.to_datetime(complete["effective_date"], errors="coerce")
        if effective.isna().any():
            errors.append("complete evidence rows require valid effective_date")
        else:
            period_mismatch = effective.dt.to_period("M").astype(str) != complete["expected_period"]
            if period_mismatch.any():
                bad = complete.loc[period_mismatch, "expected_period"].tolist()
                errors.append(f"effective_date outside expected_period: {bad}")

        for column in ("evidence_type", "source_url"):
            missing_value = complete[column].isna() | complete[column].astype(str).str.strip().eq("")
            if missing_value.any():
                errors.append(f"complete evidence rows require non-empty {column}")

        urls = complete["source_url"].fillna("").astype(str).str.strip()
        bad_urls = ~urls.str.startswith(("https://", "http://"))
        if bad_urls.any():
            errors.append("complete evidence rows require http(s) source_url")

    complete_periods = set(complete["expected_period"]) & expected_set
    pending = tuple(sorted(expected_set - complete_periods))
    eligible = not errors and not pending
    return MembershipEvidenceAudit(
        eligible=eligible,
        expected_periods=len(expected),
        complete_periods=len(complete_periods),
        pending_periods=pending,
        errors=tuple(errors),
    )


def audit_daily_limit_rule_coverage(
    prices: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    min_daily_coverage: float,
) -> LimitRuleCoverageAudit:
    """Audit stock-day price-limit provenance for a point-in-time universe.

    This function does not infer historical ST status or board rules.  It checks
    that the public input has already made those semantics explicit.  Rows that
    deliberately have no usable limit rule must set ``limit_eligible=False``
    and still provide a provenance/reason string in ``limit_rule_source``.
    """

    if not 0.0 <= min_daily_coverage <= 1.0:
        raise ValueError("min_daily_coverage must be between 0 and 1")

    diagnostics = universe_diagnostics(universe)
    errors: list[str] = []
    if not diagnostics.has_point_in_time_history:
        errors.append("universe is not genuine point-in-time history")

    required = {"date", "symbol", "limit_pct", "limit_eligible", "limit_rule_source"}
    missing = required - set(prices.columns)
    if missing:
        return LimitRuleCoverageAudit(
            eligible=False,
            minimum_daily_coverage=0.0,
            required_daily_coverage=min_daily_coverage,
            days_below_threshold=0,
            invalid_rows=len(prices),
            errors=tuple(errors + [f"prices missing columns: {sorted(missing)}"]),
            daily_coverage=pd.DataFrame(
                columns=["date", "stock_rows", "limit_eligible_rows", "limit_coverage"]
            ),
        )

    active = apply_universe_membership(prices, universe)
    if active.empty:
        return LimitRuleCoverageAudit(
            eligible=False,
            minimum_daily_coverage=0.0,
            required_daily_coverage=min_daily_coverage,
            days_below_threshold=0,
            invalid_rows=0,
            errors=tuple(errors + ["no active stock-day rows after point-in-time membership"]),
            daily_coverage=pd.DataFrame(
                columns=["date", "stock_rows", "limit_eligible_rows", "limit_coverage"]
            ),
        )

    active["date"] = pd.to_datetime(active["date"], errors="raise")
    duplicate_mask = active.duplicated(subset=["date", "symbol"], keep=False)
    if duplicate_mask.any():
        errors.append("duplicate active stock-day rows")

    eligible_values = active["limit_eligible"]
    if eligible_values.dtype == bool:
        eligible_mask = eligible_values.copy()
        bad_boolean = pd.Series(False, index=active.index)
    else:
        normalised = eligible_values.astype(str).str.strip().str.lower()
        mapping = {
            "true": True,
            "false": False,
            "1": True,
            "0": False,
            "yes": True,
            "no": False,
        }
        eligible_mask = normalised.map(mapping)
        bad_boolean = eligible_mask.isna()
        eligible_mask = eligible_mask.fillna(False).astype(bool)

    pct = pd.to_numeric(active["limit_pct"], errors="coerce")
    source = active["limit_rule_source"].fillna("").astype(str).str.strip()
    invalid_source = source.eq("")
    invalid_eligible_pct = eligible_mask & (pct.isna() | (pct <= 0) | (pct > 100))
    invalid_rows_mask = bad_boolean | invalid_source | invalid_eligible_pct
    invalid_rows = int(invalid_rows_mask.sum())
    if invalid_rows:
        errors.append(f"invalid limit-rule rows: {invalid_rows}")

    coverage_frame = pd.DataFrame(
        {
            "date": active["date"],
            "limit_eligible": eligible_mask & ~invalid_rows_mask,
        }
    )
    grouped = coverage_frame.groupby("date", as_index=False).agg(
        stock_rows=("limit_eligible", "size"),
        limit_eligible_rows=("limit_eligible", "sum"),
    )
    grouped["limit_coverage"] = grouped["limit_eligible_rows"] / grouped["stock_rows"]
    grouped = grouped.sort_values("date").reset_index(drop=True)

    minimum = float(grouped["limit_coverage"].min())
    days_below = int((grouped["limit_coverage"] < min_daily_coverage).sum())
    if days_below:
        errors.append(
            f"{days_below} trading days below required limit-rule coverage "
            f"{min_daily_coverage:.3f}"
        )

    return LimitRuleCoverageAudit(
        eligible=not errors,
        minimum_daily_coverage=minimum,
        required_daily_coverage=min_daily_coverage,
        days_below_threshold=days_below,
        invalid_rows=invalid_rows,
        errors=tuple(errors),
        daily_coverage=grouped,
    )
