from __future__ import annotations

import json
from typing import Mapping

import numpy as np
import pandas as pd

from .pit_public_materialization import _stable_hash, validate_materialized_pit_records


VALUATION_SOURCE_ID = "DERIVED_PIT_TRAILING_VALUATION"
VALUATION_PROVIDER = "DERIVED_PIT_PRICE_AND_VERSIONED_OFFICIAL_FILINGS"
VALUATION_FORMULA_VERSION = "trailing-pe-from-cumulative-eps-v1"


def _entity_from_symbol(symbol: object) -> str:
    code = "".join(ch for ch in str(symbol or "") if ch.isdigit()).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    raise ValueError(f"cannot infer market from stock symbol: {symbol}")


def _latest_fact(
    eps_facts: pd.DataFrame,
    *,
    entity_id: str,
    period_end: pd.Timestamp,
    as_of: pd.Timestamp,
) -> Mapping[str, object] | None:
    x = eps_facts[
        eps_facts["entity_id"].astype(str).eq(entity_id)
        & eps_facts["period_end"].eq(period_end)
        & eps_facts["evidence_available_date"].le(as_of)
    ].copy()
    if x.empty:
        return None
    x = x.sort_values(["evidence_available_date", "document_id", "revision_id"])
    return x.iloc[-1].to_dict()


def _ttm_eps_as_of(
    eps_facts: pd.DataFrame,
    *,
    entity_id: str,
    as_of: pd.Timestamp,
) -> tuple[float, dict[str, object]] | None:
    available = eps_facts[
        eps_facts["entity_id"].astype(str).eq(entity_id)
        & eps_facts["evidence_available_date"].le(as_of)
    ].copy()
    if available.empty:
        return None
    latest_period = available["period_end"].max()
    current = _latest_fact(
        eps_facts,
        entity_id=entity_id,
        period_end=latest_period,
        as_of=as_of,
    )
    if current is None:
        return None
    current_eps = float(current["value"])
    if latest_period.month == 12 and latest_period.day == 31:
        return current_eps, {
            "current": current,
            "prior_fy": None,
            "prior_comparable": None,
            "formula": "ANNUAL_EPS",
        }

    prior_fy_end = pd.Timestamp(year=latest_period.year - 1, month=12, day=31)
    prior_comparable_end = latest_period - pd.DateOffset(years=1)
    prior_fy = _latest_fact(
        eps_facts,
        entity_id=entity_id,
        period_end=prior_fy_end,
        as_of=as_of,
    )
    prior_comparable = _latest_fact(
        eps_facts,
        entity_id=entity_id,
        period_end=prior_comparable_end,
        as_of=as_of,
    )
    if prior_fy is None or prior_comparable is None:
        return None
    ttm = float(prior_fy["value"]) + current_eps - float(prior_comparable["value"])
    return ttm, {
        "current": current,
        "prior_fy": prior_fy,
        "prior_comparable": prior_comparable,
        "formula": "PRIOR_FY_PLUS_CURRENT_CUMULATIVE_MINUS_PRIOR_COMPARABLE_CUMULATIVE",
    }


def build_trailing_valuation_rail(
    *,
    filing_facts: pd.DataFrame,
    stock_prices: pd.DataFrame,
) -> pd.DataFrame:
    """Build a PIT trailing-PE rail from unadjusted close and as-of EPS facts."""

    fact_required = {
        "entity_id",
        "period_end",
        "fact_type",
        "value",
        "unit",
        "evidence_available_date",
        "document_id",
        "revision_id",
        "document_sha256",
    }
    price_required = {"date", "symbol", "close", "provider"}
    fact_missing = fact_required - set(filing_facts.columns)
    price_missing = price_required - set(stock_prices.columns)
    if fact_missing:
        raise ValueError(f"filing facts missing columns: {sorted(fact_missing)}")
    if price_missing:
        raise ValueError(f"stock prices missing columns: {sorted(price_missing)}")

    eps = filing_facts[filing_facts["fact_type"].astype(str).eq("BASIC_EPS")].copy()
    eps["period_end"] = pd.to_datetime(eps["period_end"], errors="raise").dt.normalize()
    eps["evidence_available_date"] = pd.to_datetime(
        eps["evidence_available_date"], errors="raise"
    ).dt.normalize()
    eps["value"] = pd.to_numeric(eps["value"], errors="raise")
    if not eps.empty and not eps["unit"].astype(str).eq("CNY_PER_SHARE").all():
        raise ValueError("BASIC_EPS filing facts must use CNY_PER_SHARE")

    prices = stock_prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
    prices["close"] = pd.to_numeric(prices["close"], errors="raise")
    prices["entity_id"] = prices["symbol"].map(_entity_from_symbol)
    if prices.duplicated(["entity_id", "date"]).any():
        raise ValueError("stock price history contains duplicate entity/date rows")

    rows: list[dict[str, object]] = []
    for entity, part in prices.sort_values(["entity_id", "date"]).groupby("entity_id"):
        for _, price in part.iterrows():
            date = pd.Timestamp(price["date"]).normalize()
            ttm_result = _ttm_eps_as_of(eps, entity_id=str(entity), as_of=date)
            if ttm_result is None:
                rows.append(
                    {
                        "date": date,
                        "entity_id": str(entity),
                        "close": float(price["close"]),
                        "price_provider": str(price["provider"]),
                        "ttm_eps": np.nan,
                        "trailing_pe": np.nan,
                        "denominator_document_id": None,
                        "denominator_revision_id": None,
                        "denominator_document_sha256": None,
                        "formula_trace": None,
                    }
                )
                continue
            ttm_eps, trace = ttm_result
            current = trace["current"]
            pe = float(price["close"]) / ttm_eps if ttm_eps != 0 else np.nan
            trace_payload = {
                "formula": trace["formula"],
                "current_document_id": str(current["document_id"]),
                "current_revision_id": str(current["revision_id"]),
                "current_document_sha256": str(current["document_sha256"]),
                "prior_fy_document_id": (
                    str(trace["prior_fy"]["document_id"]) if trace["prior_fy"] else None
                ),
                "prior_comparable_document_id": (
                    str(trace["prior_comparable"]["document_id"])
                    if trace["prior_comparable"]
                    else None
                ),
                "as_of_date": str(date.date()),
            }
            rows.append(
                {
                    "date": date,
                    "entity_id": str(entity),
                    "close": float(price["close"]),
                    "price_provider": str(price["provider"]),
                    "ttm_eps": float(ttm_eps),
                    "trailing_pe": float(pe) if np.isfinite(pe) else np.nan,
                    "denominator_document_id": str(current["document_id"]),
                    "denominator_revision_id": str(current["revision_id"]),
                    "denominator_document_sha256": str(current["document_sha256"]),
                    "formula_trace": json.dumps(trace_payload, ensure_ascii=False, sort_keys=True),
                }
            )
    rail = pd.DataFrame(rows)
    if rail.empty:
        return rail
    rail = rail.sort_values(["entity_id", "date"]).reset_index(drop=True)
    rail["valuation_change_20d"] = rail.groupby("entity_id")["trailing_pe"].transform(
        lambda values: values / values.shift(20) - 1.0
    )
    rail["source_identity"] = VALUATION_SOURCE_ID
    rail["provider"] = VALUATION_PROVIDER
    rail["formula_version"] = VALUATION_FORMULA_VERSION
    return rail


def valuation_rail_to_pit_evidence(rail: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date",
        "entity_id",
        "close",
        "price_provider",
        "ttm_eps",
        "trailing_pe",
        "valuation_change_20d",
        "denominator_document_id",
        "denominator_revision_id",
        "denominator_document_sha256",
        "formula_trace",
        "source_identity",
        "provider",
        "formula_version",
    }
    missing = required - set(rail.columns)
    if missing:
        raise ValueError(f"valuation rail missing columns: {sorted(missing)}")
    rows: list[dict[str, object]] = []
    usable = rail[
        pd.to_numeric(rail["trailing_pe"], errors="coerce").notna()
        & pd.to_numeric(rail["valuation_change_20d"], errors="coerce").notna()
    ]
    for _, item in usable.iterrows():
        date = pd.Timestamp(item["date"]).normalize()
        payload = {
            "trailing_pe": float(item["trailing_pe"]),
            "valuation_change_20d": float(item["valuation_change_20d"]),
            "ttm_eps": float(item["ttm_eps"]),
            "close": float(item["close"]),
            "formula_version": str(item["formula_version"]),
        }
        provenance = {
            "source_identity": VALUATION_SOURCE_ID,
            "price_provider": str(item["price_provider"]),
            "price_semantics": "UNADJUSTED_DAILY_CLOSE",
            "denominator_document_id": str(item["denominator_document_id"]),
            "denominator_revision_id": str(item["denominator_revision_id"]),
            "denominator_document_sha256": str(item["denominator_document_sha256"]),
            "formula_trace": json.loads(str(item["formula_trace"])),
            "formula_version": str(item["formula_version"]),
            "future_filing_denominator_used": False,
            "hindsight_backfill": False,
        }
        identity = {
            "entity": str(item["entity_id"]),
            "date": str(date.date()),
            "denominator_document_id": str(item["denominator_document_id"]),
            "formula_version": str(item["formula_version"]),
        }
        rows.append(
            {
                "evidence_id": f"trailing-valuation:{_stable_hash(identity)}",
                "entity_id": str(item["entity_id"]),
                "evidence_type": "TRAILING_VALUATION_CHANGE",
                "event_date": date,
                "evidence_available_date": date,
                "source_identity": VALUATION_SOURCE_ID,
                "provider": VALUATION_PROVIDER,
                "document_id": str(item["denominator_document_id"]),
                "revision_id": (
                    f"{item['denominator_revision_id']}:PRICE_DATE:{date.date()}"
                ),
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash({"identity": identity, "payload": payload}),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        )
    if not rows:
        return pd.DataFrame()
    return validate_materialized_pit_records(pd.DataFrame(rows))


__all__ = [
    "VALUATION_SOURCE_ID",
    "VALUATION_PROVIDER",
    "VALUATION_FORMULA_VERSION",
    "build_trailing_valuation_rail",
    "valuation_rail_to_pit_evidence",
]
