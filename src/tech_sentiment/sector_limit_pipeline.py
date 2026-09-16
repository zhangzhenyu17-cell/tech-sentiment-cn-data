from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .sector_data_gate import LimitRuleCoverageAudit, audit_daily_limit_rule_coverage
from .sector_limit_semantics import enrich_baostock_structural_limit_rows
from .sector_special_day_semantics import SpecialDayAudit, derive_special_day_status


@dataclass(frozen=True)
class SectorLimitPipelineResult:
    rows: pd.DataFrame
    special_day_audit: SpecialDayAudit
    coverage_audit: LimitRuleCoverageAudit


def build_and_audit_sector_limit_rows(
    daily_rows: pd.DataFrame,
    stock_basic: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    overrides: pd.DataFrame | None = None,
    min_daily_coverage: float = 0.95,
) -> SectorLimitPipelineResult:
    """Build stock-day limit provenance then run the existing point-in-time gate.

    This is an input-qualification pipeline only. It does not inspect returns or
    sector-model outcomes. The output preserves the original raw daily columns and
    adds lifecycle/special-day and structural-limit provenance before coverage audit.
    """

    with_special, special_audit = derive_special_day_status(
        daily_rows,
        stock_basic,
        overrides=overrides,
    )
    enriched = enrich_baostock_structural_limit_rows(with_special)
    prices = enriched.copy()
    prices["symbol"] = prices["code"].astype(str).str.replace(r"^(?:sh|sz)\.", "", regex=True)
    coverage = audit_daily_limit_rule_coverage(
        prices,
        universe,
        min_daily_coverage=min_daily_coverage,
    )
    return SectorLimitPipelineResult(
        rows=enriched,
        special_day_audit=special_audit,
        coverage_audit=coverage,
    )
