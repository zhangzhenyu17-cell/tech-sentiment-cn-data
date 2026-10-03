from __future__ import annotations

from typing import Mapping

import pandas as pd

from .official_filing_extended_pit import _direct_amount_value_in_statement_scope
from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _has_explicit_unit_declaration,
    _normalize_text_lines,
    filing_period_end_from_title,
)

DECISION_DRIVER_PARSER_VERSION = "official-filing-decision-drivers-v2-cngaap-tax-prefix"

# Direct consolidated income-statement observations only. Names deliberately
# retain CN_GAAP so downstream research cannot silently relabel them as EBIT,
# NOPAT or cash tax.
DECISION_DRIVER_FACT_LABELS: Mapping[str, tuple[str, ...]] = {
    "OPERATING_PROFIT_CN_GAAP": ("营业利润",),
    "TOTAL_PROFIT_CN_GAAP": ("利润总额",),
    "INCOME_TAX_EXPENSE_CN_GAAP": ("所得税费用", "减:所得税费用"),
}


def extract_decision_driver_facts(text: str) -> dict[str, float]:
    lines = _normalize_text_lines(text)
    if not _has_explicit_unit_declaration(lines):
        raise ValueError(
            "decision-driver filing text does not contain an explicit table unit declaration"
        )

    facts: dict[str, float] = {}
    for fact_type, labels in DECISION_DRIVER_FACT_LABELS.items():
        value = _direct_amount_value_in_statement_scope(
            lines,
            labels,
            statement_token="利润表",
        )
        if value is not None:
            facts[fact_type] = float(value)

    if not facts:
        raise ValueError(
            "official filing has no decision-driver primitives with locally proven CNY units"
        )
    return facts


def build_decision_driver_fact_rows(
    *,
    entity_id: str,
    title: str,
    evidence_available_date: object,
    publication_timestamp: object,
    source_identity: str,
    provider: str,
    document_id: str,
    revision_id: str,
    document_url: str,
    document_sha256: str,
    text: str,
) -> pd.DataFrame:
    period_end = filing_period_end_from_title(title)
    facts = extract_decision_driver_facts(text)
    publication = pd.Timestamp(pd.to_datetime(publication_timestamp, errors="raise"))
    rows: list[dict[str, object]] = []
    for fact_type, value in sorted(facts.items()):
        rows.append(
            {
                "entity_id": str(entity_id),
                "period_end": period_end,
                "fact_type": fact_type,
                "value": float(value),
                "unit": "CNY",
                "evidence_available_date": pd.Timestamp(evidence_available_date).normalize(),
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": DECISION_DRIVER_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS))


__all__ = [
    "DECISION_DRIVER_FACT_LABELS",
    "DECISION_DRIVER_PARSER_VERSION",
    "build_decision_driver_fact_rows",
    "extract_decision_driver_facts",
]
