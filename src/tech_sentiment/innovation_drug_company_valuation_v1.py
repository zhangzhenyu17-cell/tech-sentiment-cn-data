from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .trailing_valuation_pit import (
    VALUATION_FORMULA_VERSION,
    VALUATION_PROVIDER,
    VALUATION_SOURCE_ID,
    build_trailing_valuation_rail,
    valuation_rail_to_pit_evidence,
)

BUNDLE_VERSION = "innovation-drug-company-valuation-raw-v1"
TARGET_SYMBOL = "600276"
TARGET_ENTITY = "600276.SH"


@dataclass(frozen=True)
class CompanyValuationRawV1:
    rail: pd.DataFrame
    evidence: pd.DataFrame
    summary: dict[str, object]


def build_company_valuation_raw_v1(
    *,
    filing_facts: pd.DataFrame,
    stock_prices: pd.DataFrame,
    trading_dates: Iterable[object],
    start_date: object,
    end_date: object,
) -> CompanyValuationRawV1:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")

    facts = filing_facts.copy()
    required_fact_cols = {"entity_id", "fact_type", "evidence_available_date"}
    missing = required_fact_cols - set(facts.columns)
    if missing:
        raise ValueError(f"filing facts missing columns: {sorted(missing)}")
    entities = set(facts["entity_id"].dropna().astype(str))
    if entities != {TARGET_ENTITY}:
        raise ValueError(f"valuation input must be exact {TARGET_ENTITY}: {sorted(entities)}")
    eps = facts.loc[facts["fact_type"].astype(str).eq("BASIC_EPS")]
    if eps.empty:
        raise ValueError("exact 600276 filing facts contain no BASIC_EPS")

    prices = stock_prices.copy()
    required_price_cols = {"date", "symbol", "close", "provider"}
    price_missing = required_price_cols - set(prices.columns)
    if price_missing:
        raise ValueError(f"stock prices missing columns: {sorted(price_missing)}")
    symbols = set(
        prices["symbol"].dropna().astype(str).str.extract(r"(\d{6})", expand=False)
    )
    if symbols != {TARGET_SYMBOL}:
        raise ValueError(
            f"valuation price input must be exact {TARGET_SYMBOL}: {sorted(symbols)}"
        )

    rail = build_trailing_valuation_rail(
        filing_facts=facts,
        stock_prices=prices,
        trading_dates=trading_dates,
    )
    if not rail.empty:
        rail = rail.loc[
            pd.to_datetime(rail["date"], errors="raise")
            .dt.normalize()
            .between(start, end)
        ].reset_index(drop=True)
        if set(rail["entity_id"].astype(str)) != {TARGET_ENTITY}:
            raise ValueError("valuation rail escaped exact 600276 entity scope")
        if set(rail["source_identity"].astype(str)) != {VALUATION_SOURCE_ID}:
            raise ValueError("valuation source identity drift")
        if set(rail["formula_version"].astype(str)) != {VALUATION_FORMULA_VERSION}:
            raise ValueError("valuation formula version drift")

    evidence = (
        valuation_rail_to_pit_evidence(rail) if not rail.empty else pd.DataFrame()
    )
    usable = (
        rail["trailing_pe"].notna()
        & rail["valuation_reference_date_20d"].notna()
        & rail["trailing_pe_20d_reference"].notna()
        & rail["valuation_change_20d"].notna()
        if not rail.empty
        else pd.Series(dtype=bool)
    )
    summary: dict[str, object] = {
        "bundle_version": BUNDLE_VERSION,
        "symbol": TARGET_SYMBOL,
        "entity_id": TARGET_ENTITY,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "source_identity": VALUATION_SOURCE_ID,
        "provider": VALUATION_PROVIDER,
        "formula_version": VALUATION_FORMULA_VERSION,
        "price_semantics": "UNADJUSTED_DAILY_CLOSE",
        "valuation_reference_semantics": (
            "EXACT_REAL_TRADING_CALENDAR_T_MINUS_20_NO_FILL"
        ),
        "rail_rows": int(len(rail)),
        "usable_rows": int(usable.sum()) if len(rail) else 0,
        "evidence_rows": int(len(evidence)),
        "history_percentile_computed": False,
        "valuation_state_classified": False,
        "private_thresholds_used": False,
        "forward_prices_or_returns_used": False,
        "outcome_read": False,
        "predictive_research_run": False,
        "parameter_search_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "price_fill_used": False,
        "historical_backfill_of_future_information": False,
    }
    return CompanyValuationRawV1(rail=rail, evidence=evidence, summary=summary)


__all__ = [
    "BUNDLE_VERSION",
    "TARGET_ENTITY",
    "TARGET_SYMBOL",
    "CompanyValuationRawV1",
    "build_company_valuation_raw_v1",
]
