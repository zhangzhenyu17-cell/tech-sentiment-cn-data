from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

import pandas as pd

from .official_filing_facts import (
    DERIVED_FUNDAMENTAL_PROVIDER,
    DERIVED_FUNDAMENTAL_SOURCE_ID,
    FILING_PARSER_VERSION,
    latest_filing_fact_as_of,
)
from .pit_public_materialization import (
    REQUIRED_PIT_COLUMNS,
    _stable_hash,
    validate_materialized_pit_records,
)


CONTRACT_ID = "FUNDAMENTAL_PIT_STATE_CONTRACT_V1"
FORMULA_VERSION = "fundamental-pit-state-v1"
REQUIRED_FACTS = (
    "OPERATING_REVENUE",
    "NET_PROFIT_PARENT",
    "OPERATING_CASH_FLOW_NET",
    "NET_PROFIT_MARGIN",
)
FUNDAMENTAL_STATE_EVIDENCE_COLUMNS = tuple(REQUIRED_PIT_COLUMNS) + (
    "evidence_payload",
    "source_url_identity",
)
FUNDAMENTAL_STATE_COVERAGE_COLUMNS = (
    "source_identity",
    "entity_id",
    "period_end",
    "evidence_available_date",
    "state",
    "complete_required_comparable_facts",
    "missing_requirements",
    "contract_id",
)


@dataclass(frozen=True)
class FundamentalStateMaterializationResult:
    evidence: pd.DataFrame
    coverage: pd.DataFrame
    summary: dict[str, object]


def _latest_fact(
    facts: pd.DataFrame,
    *,
    entity_id: str,
    period_end: pd.Timestamp,
    fact_type: str,
    as_of: pd.Timestamp,
) -> Mapping[str, object] | None:
    return latest_filing_fact_as_of(
        facts,
        entity_id=entity_id,
        fact_type=fact_type,
        period_end=period_end,
        as_of=as_of,
    )


def _facts_for_period_as_of(
    facts: pd.DataFrame,
    *,
    entity_id: str,
    period_end: pd.Timestamp,
    as_of: pd.Timestamp,
) -> tuple[dict[str, Mapping[str, object]], list[str]]:
    selected: dict[str, Mapping[str, object]] = {}
    missing: list[str] = []
    for fact_type in REQUIRED_FACTS:
        row = _latest_fact(
            facts,
            entity_id=entity_id,
            period_end=period_end,
            fact_type=fact_type,
            as_of=as_of,
        )
        if row is None:
            missing.append(fact_type)
        else:
            selected[fact_type] = row
    return selected, missing


def _value(rows: Mapping[str, Mapping[str, object]], fact_type: str) -> float:
    return float(rows[fact_type]["value"])


def classify_complete_accounting_state(
    current: Mapping[str, Mapping[str, object]],
    prior: Mapping[str, Mapping[str, object]],
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    """Apply the frozen V1 accounting-state contract to complete comparable facts.

    V1 intentionally uses only sign and exact directional comparisons.  It has
    no return-optimized magnitude threshold and consumes no market price/outcome.
    """

    revenue = _value(current, "OPERATING_REVENUE")
    profit = _value(current, "NET_PROFIT_PARENT")
    cash = _value(current, "OPERATING_CASH_FLOW_NET")
    margin = _value(current, "NET_PROFIT_MARGIN")
    prior_revenue = _value(prior, "OPERATING_REVENUE")
    prior_profit = _value(prior, "NET_PROFIT_PARENT")
    prior_cash = _value(prior, "OPERATING_CASH_FLOW_NET")
    prior_margin = _value(prior, "NET_PROFIT_MARGIN")

    directions = {
        "revenue": "UP_OR_FLAT" if revenue >= prior_revenue else "DOWN",
        "profit": "UP_OR_FLAT" if profit >= prior_profit else "DOWN",
        "operating_cash_flow": "UP_OR_FLAT" if cash >= prior_cash else "DOWN",
        "net_profit_margin": "UP_OR_FLAT" if margin >= prior_margin else "DOWN",
    }

    if profit < 0:
        return "FUNDAMENTAL_FAIL", ("CURRENT_NET_LOSS",), directions

    if (
        revenue < prior_revenue
        and profit < prior_profit
        and cash < prior_cash
        and margin < prior_margin
    ):
        return "FUNDAMENTAL_FAIL", ("BROAD_BASED_DETERIORATION",), directions

    if profit > 0 and cash < 0 and prior_cash >= 0 and cash < prior_cash:
        return "FUNDAMENTAL_FAIL", ("PROFIT_CASH_DIVERGENCE",), directions

    if profit < prior_profit and margin < prior_margin and cash < prior_cash:
        return "FUNDAMENTAL_FAIL", ("PROFIT_MARGIN_CASH_DETERIORATION",), directions

    if (
        profit > 0
        and cash >= 0
        and revenue >= prior_revenue
        and profit >= prior_profit
        and cash >= prior_cash
        and margin >= prior_margin
    ):
        return (
            "FUNDAMENTAL_PASS",
            ("ALL_CORE_DIMENSIONS_NON_DETERIORATING",),
            directions,
        )

    return "FUNDAMENTAL_WATCH", ("QUALIFIED_MIXED_ACCOUNTING_STATE",), directions


def _support_identity(rows: Mapping[str, Mapping[str, object]]) -> list[dict[str, str]]:
    support: list[dict[str, str]] = []
    for fact_type in REQUIRED_FACTS:
        row = rows[fact_type]
        support.append(
            {
                "fact_type": fact_type,
                "document_id": str(row["document_id"]),
                "revision_id": str(row["revision_id"]),
                "publication_timestamp": str(row["publication_timestamp"]),
                "document_sha256": str(row["document_sha256"]),
                "source_identity": str(row["source_identity"]),
                "provider": str(row["provider"]),
            }
        )
    return support


def materialize_fundamental_state_evidence(
    facts: pd.DataFrame,
    *,
    target_start_date: object | None = None,
    target_end_date: object | None = None,
) -> FundamentalStateMaterializationResult:
    required = {
        "entity_id",
        "period_end",
        "fact_type",
        "value",
        "unit",
        "evidence_available_date",
        "publication_timestamp",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "document_url",
        "document_sha256",
        "parser_version",
    }
    missing_columns = required - set(facts.columns)
    if missing_columns:
        raise ValueError(f"filing facts missing columns: {sorted(missing_columns)}")
    if facts.empty:
        return FundamentalStateMaterializationResult(
            evidence=pd.DataFrame(columns=list(FUNDAMENTAL_STATE_EVIDENCE_COLUMNS)),
            coverage=pd.DataFrame(columns=list(FUNDAMENTAL_STATE_COVERAGE_COLUMNS)),
            summary={
                "contract_id": CONTRACT_ID,
                "formula_version": FORMULA_VERSION,
                "readiness_state": "DATA_INSUFFICIENT",
                "qualified_state_records": 0,
                "data_insufficient_records": 0,
                "future_prices_or_returns_used": False,
                "parameter_search_used": False,
            },
        )

    x = facts.copy()
    x["period_end"] = pd.to_datetime(x["period_end"], errors="raise").dt.normalize()
    x["evidence_available_date"] = pd.to_datetime(
        x["evidence_available_date"], errors="raise"
    ).dt.normalize()
    pd.to_datetime(x["publication_timestamp"], errors="raise", utc=True)
    if x.duplicated(["entity_id", "document_id", "revision_id", "fact_type"]).any():
        raise ValueError("filing facts contain duplicate document/fact identities")
    if x["document_sha256"].isna().any() or x["document_sha256"].astype(str).str.strip().eq("").any():
        raise ValueError("filing facts require immutable document SHA256 identity")

    target_start = (
        pd.Timestamp(target_start_date).normalize() if target_start_date is not None else None
    )
    target_end = (
        pd.Timestamp(target_end_date).normalize() if target_end_date is not None else None
    )

    evidence_rows: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    for entity_id in sorted(x["entity_id"].astype(str).unique()):
        entity = x[x["entity_id"].astype(str).eq(entity_id)].copy()
        periods = sorted(pd.Timestamp(value).normalize() for value in entity["period_end"].unique())
        for period_end in periods:
            if target_start is not None and period_end < target_start - pd.DateOffset(years=1):
                continue
            if target_end is not None and period_end > target_end:
                continue
            current_rows = entity[entity["period_end"].eq(period_end)]
            for cutoff in sorted(current_rows["evidence_available_date"].unique()):
                as_of = pd.Timestamp(cutoff).normalize()
                if target_end is not None and as_of > target_end:
                    continue
                prior_end = period_end - pd.DateOffset(years=1)
                current, current_missing = _facts_for_period_as_of(
                    x,
                    entity_id=entity_id,
                    period_end=period_end,
                    as_of=as_of,
                )
                prior, prior_missing = _facts_for_period_as_of(
                    x,
                    entity_id=entity_id,
                    period_end=prior_end,
                    as_of=as_of,
                )
                missing = [f"current:{name}" for name in current_missing] + [
                    f"prior:{name}" for name in prior_missing
                ]
                if missing:
                    state = "DATA_INSUFFICIENT"
                    reasons = ("REQUIRED_COMPARABLE_FACTS_MISSING",)
                    directions: dict[str, str] = {}
                    availability_state = "DATA_INSUFFICIENT"
                    support: list[dict[str, str]] = []
                else:
                    state, reasons, directions = classify_complete_accounting_state(current, prior)
                    availability_state = "HISTORICAL_RECONSTRUCTABLE"
                    support = _support_identity(current) + _support_identity(prior)

                payload = {
                    "fundamental_state": state,
                    "contract_id": CONTRACT_ID,
                    "formula_version": FORMULA_VERSION,
                    "period_end": str(period_end.date()),
                    "prior_comparable_period_end": str(prior_end.date()),
                    "classification_reasons": list(reasons),
                    "directions": directions,
                    "missing_requirements": missing,
                    "support": support,
                    "price_or_return_used": False,
                    "parameter_search_used": False,
                }
                support_token = _stable_hash(payload)
                provenance = {
                    "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                    "provider": DERIVED_FUNDAMENTAL_PROVIDER,
                    "contract_id": CONTRACT_ID,
                    "formula_version": FORMULA_VERSION,
                    "filing_parser_version": FILING_PARSER_VERSION,
                    "selection_semantics": "LATEST_AVAILABLE_DATE_THEN_LATEST_OFFICIAL_PUBLICATION_TIMESTAMP",
                    "prior_comparable_semantics": "EXACT_SAME_REPORT_PERIOD_ONE_CALENDAR_YEAR_EARLIER",
                    "append_only": True,
                    "later_restatements_do_not_rewrite_earlier_as_of_state": True,
                    "future_prices_or_returns_used": False,
                    "parameter_search_used": False,
                    "support": support,
                }
                document_id = f"FUNDAMENTAL_STATE:{entity_id}:{period_end.date()}:{support_token[:16]}"
                evidence_rows.append(
                    {
                        "evidence_id": f"fundamental-state:{_stable_hash((entity_id, str(period_end.date()), str(as_of.date()), support_token))}",
                        "entity_id": entity_id,
                        "evidence_type": "FUNDAMENTAL_STATE",
                        "event_date": period_end,
                        "evidence_available_date": as_of,
                        "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                        "provider": DERIVED_FUNDAMENTAL_PROVIDER,
                        "document_id": document_id,
                        "revision_id": f"{CONTRACT_ID}:{support_token}",
                        "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                        "ingestion_identity": _stable_hash(
                            {
                                "entity_id": entity_id,
                                "period_end": str(period_end.date()),
                                "as_of": str(as_of.date()),
                                "payload": payload,
                            }
                        ),
                        "availability_state": availability_state,
                        "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        "source_url_identity": "DERIVED_FROM_VERSIONED_OFFICIAL_FILINGS",
                    }
                )
                coverage_rows.append(
                    {
                        "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                        "entity_id": entity_id,
                        "period_end": period_end,
                        "evidence_available_date": as_of,
                        "state": state,
                        "complete_required_comparable_facts": not missing,
                        "missing_requirements": ";".join(missing),
                        "contract_id": CONTRACT_ID,
                    }
                )

    evidence = (
        validate_materialized_pit_records(
            pd.DataFrame(evidence_rows, columns=list(FUNDAMENTAL_STATE_EVIDENCE_COLUMNS))
        )
        if evidence_rows
        else pd.DataFrame(columns=list(FUNDAMENTAL_STATE_EVIDENCE_COLUMNS))
    )
    coverage = pd.DataFrame(
        coverage_rows, columns=list(FUNDAMENTAL_STATE_COVERAGE_COLUMNS)
    )
    qualified = int(
        evidence["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE").sum()
    ) if len(evidence) else 0
    insufficient = int(
        evidence["availability_state"].astype(str).eq("DATA_INSUFFICIENT").sum()
    ) if len(evidence) else 0
    entities = sorted(x["entity_id"].astype(str).unique())
    latest_complete_by_entity = []
    if len(coverage):
        for entity_id in entities:
            rows = coverage[coverage["entity_id"].astype(str).eq(entity_id)].sort_values(
                ["evidence_available_date", "period_end"]
            )
            latest_complete_by_entity.append(
                bool(len(rows)) and bool(rows.iloc[-1]["complete_required_comparable_facts"])
            )
    all_latest_complete = bool(entities) and len(latest_complete_by_entity) == len(entities) and all(latest_complete_by_entity)
    summary = {
        "contract_id": CONTRACT_ID,
        "formula_version": FORMULA_VERSION,
        "symbols_or_entities": len(entities),
        "state_records": int(len(evidence)),
        "qualified_state_records": qualified,
        "data_insufficient_records": insufficient,
        "latest_required_comparable_coverage_complete": all_latest_complete,
        "readiness_state": "QUALIFIED_INPUT" if all_latest_complete and qualified > 0 else (
            "PARTIAL_COVERAGE" if qualified > 0 else "DATA_INSUFFICIENT"
        ),
        "threshold_policy": "SIGN_AND_EXACT_DIRECTION_ONLY_NO_RETURN_OPTIMIZATION",
        "future_prices_or_returns_used": False,
        "parameter_search_used": False,
        "predictive_model_created": False,
    }
    return FundamentalStateMaterializationResult(
        evidence=evidence,
        coverage=coverage,
        summary=summary,
    )


__all__ = [
    "CONTRACT_ID",
    "FORMULA_VERSION",
    "REQUIRED_FACTS",
    "FUNDAMENTAL_STATE_EVIDENCE_COLUMNS",
    "FUNDAMENTAL_STATE_COVERAGE_COLUMNS",
    "FundamentalStateMaterializationResult",
    "classify_complete_accounting_state",
    "materialize_fundamental_state_evidence",
]
