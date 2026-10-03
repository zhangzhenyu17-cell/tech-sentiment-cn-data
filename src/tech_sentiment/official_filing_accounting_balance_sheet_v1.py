from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

from .official_filing_extended_pit import (
    _compact_line,
    _direct_amount_value_after_label,
    _explicit_statement_unit,
    _statement_boundaries,
    _statement_header_has_note_column,
)
from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _normalize_text_lines,
    filing_period_end_from_title,
)

ACCOUNTING_BALANCE_SHEET_PARSER_VERSION = (
    "official-filing-accounting-balance-sheet-v1-direct-consolidated"
)

# Direct consolidated balance-sheet observations only. The names below are
# public raw statement facts. This module deliberately does not classify any
# fact as operating/financing, does not infer excess cash, and does not build
# debt, NOA, invested capital, NOPAT, ROIC, RNOA, or any private model field.
ACCOUNTING_BALANCE_SHEET_FACT_LABELS: Mapping[str, tuple[str, ...]] = {
    "TOTAL_LIABILITIES": ("负债合计",),
    "TOTAL_EQUITY": ("所有者权益合计", "股东权益合计"),
    "MINORITY_INTEREST": ("少数股东权益",),
    "TOTAL_CURRENT_ASSETS": ("流动资产合计",),
    "TOTAL_NON_CURRENT_ASSETS": ("非流动资产合计",),
    "TOTAL_CURRENT_LIABILITIES": ("流动负债合计",),
    "TOTAL_NON_CURRENT_LIABILITIES": ("非流动负债合计",),
    "MONETARY_FUNDS": ("货币资金",),
    "TRADING_FINANCIAL_ASSETS": ("交易性金融资产",),
    "DERIVATIVE_FINANCIAL_ASSETS": ("衍生金融资产",),
    "DEBT_INVESTMENTS": ("债权投资",),
    "OTHER_DEBT_INVESTMENTS": ("其他债权投资",),
    "OTHER_EQUITY_INSTRUMENT_INVESTMENTS": ("其他权益工具投资",),
    "OTHER_NON_CURRENT_FINANCIAL_ASSETS": ("其他非流动金融资产",),
    "SHORT_TERM_BORROWINGS": ("短期借款",),
    "CURRENT_PORTION_NON_CURRENT_LIABILITIES": ("一年内到期的非流动负债",),
    "LONG_TERM_BORROWINGS": ("长期借款",),
    "BONDS_PAYABLE": ("应付债券",),
    "LEASE_LIABILITIES": ("租赁负债",),
    "TRADING_FINANCIAL_LIABILITIES": ("交易性金融负债",),
    "DERIVATIVE_FINANCIAL_LIABILITIES": ("衍生金融负债",),
    "NOTES_RECEIVABLE": ("应收票据",),
    "ACCOUNTS_RECEIVABLE": ("应收账款",),
    "RECEIVABLES_FINANCING": ("应收款项融资",),
    "PREPAYMENTS": ("预付款项",),
    "INVENTORIES": ("存货",),
    "CONTRACT_ASSETS": ("合同资产",),
    "NOTES_PAYABLE": ("应付票据",),
    "ACCOUNTS_PAYABLE": ("应付账款",),
    "ADVANCES_FROM_CUSTOMERS": ("预收款项",),
    "CONTRACT_LIABILITIES": ("合同负债",),
    "EMPLOYEE_BENEFITS_PAYABLE": ("应付职工薪酬",),
    "TAXES_PAYABLE": ("应交税费",),
    "FIXED_ASSETS": ("固定资产",),
    "CONSTRUCTION_IN_PROGRESS": ("在建工程",),
    "RIGHT_OF_USE_ASSETS": ("使用权资产",),
    "INTANGIBLE_ASSETS": ("无形资产",),
    "GOODWILL": ("商誉",),
    "DEVELOPMENT_EXPENDITURE": ("开发支出",),
    "LONG_TERM_PREPAID_EXPENSES": ("长期待摊费用",),
    "OTHER_RECEIVABLES": ("其他应收款",),
    "OTHER_PAYABLES": ("其他应付款",),
    "LONG_TERM_RECEIVABLES": ("长期应收款",),
    "LONG_TERM_EQUITY_INVESTMENTS": ("长期股权投资",),
    "INVESTMENT_PROPERTY": ("投资性房地产",),
    "DEFERRED_TAX_ASSETS": ("递延所得税资产",),
    "DEFERRED_TAX_LIABILITIES": ("递延所得税负债",),
    "OTHER_CURRENT_ASSETS": ("其他流动资产",),
    "OTHER_CURRENT_LIABILITIES": ("其他流动负债",),
    "OTHER_NON_CURRENT_ASSETS": ("其他非流动资产",),
    "OTHER_NON_CURRENT_LIABILITIES": ("其他非流动负债",),
}


def _direct_consolidated_balance_sheet_value(
    lines: list[str],
    labels: Iterable[str],
) -> float | None:
    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != "资产负债表":
            continue
        if "合并资产负债表" not in _compact_line(lines[start]):
            continue
        end = boundaries[offset + 1][0] if offset + 1 < len(boundaries) else len(lines)
        unit = _explicit_statement_unit(lines, start=start, end=end)
        if unit is None:
            continue
        has_note_column = _statement_header_has_note_column(
            lines,
            start=start,
            end=end,
        )
        value = _direct_amount_value_after_label(
            lines[start:end],
            labels,
            unit_override=unit,
            note_column_override=has_note_column,
        )
        if value is not None:
            return float(value)
    return None


def extract_accounting_balance_sheet_facts(text: str) -> dict[str, float]:
    lines = _normalize_text_lines(text)
    facts: dict[str, float] = {}
    for fact_type, labels in ACCOUNTING_BALANCE_SHEET_FACT_LABELS.items():
        value = _direct_consolidated_balance_sheet_value(lines, labels)
        if value is not None:
            facts[fact_type] = float(value)
    if not facts:
        raise ValueError(
            "official filing has no direct consolidated balance-sheet primitives "
            "with locally proven CNY units"
        )
    return facts


def build_accounting_balance_sheet_fact_rows(
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
    facts = extract_accounting_balance_sheet_facts(text)
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
                "evidence_available_date": pd.Timestamp(
                    evidence_available_date
                ).normalize(),
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": ACCOUNTING_BALANCE_SHEET_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS))


__all__ = [
    "ACCOUNTING_BALANCE_SHEET_FACT_LABELS",
    "ACCOUNTING_BALANCE_SHEET_PARSER_VERSION",
    "build_accounting_balance_sheet_fact_rows",
    "extract_accounting_balance_sheet_facts",
]
