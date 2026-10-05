from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from .official_filing_extended_pit import (
    _compact_line,
    _direct_amount_value_after_label,
    _explicit_statement_unit,
    _ordered_numeric_or_dash_cells,
    _physical_line_starts_label,
    _statement_boundaries,
    _statement_header_has_note_column,
    _target_logical_row_window,
)
from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _AMOUNT_UNIT_SCALE,
    _normalize_text_lines,
    _parse_numeric_token,
    _wrapped_label_match,
    filing_period_end_from_title,
)

ACCOUNTING_BALANCE_SHEET_PARSER_VERSION = (
    "official-filing-accounting-balance-sheet-v1-direct-consolidated"
)
ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION = (
    "official-filing-accounting-balance-sheet-row-evidence-v1"
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


def _direct_consolidated_balance_sheet_row_evidence(
    lines: list[str],
    labels: Iterable[str],
) -> dict[str, Any] | None:
    """Capture source-row cell evidence without interpreting dash/blank as zero.

    The row must be in an explicitly unit-scoped consolidated balance sheet.
    Current/prior cell ownership is recorded only when the same column-layout
    constraints used by the direct numeric parser are provable.  A DASH cell is
    preserved as a source token and never converted to a numeric value.
    """

    label_options = tuple(str(label) for label in labels)
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
        scale = _AMOUNT_UNIT_SCALE.get(str(unit))
        if scale is None:
            continue
        has_note_column = _statement_header_has_note_column(lines, start=start, end=end)
        block = lines[start:end]
        for index in range(len(block)):
            if not _physical_line_starts_label(block[index], label_options):
                continue
            logical_row = _target_logical_row_window(block, index, label_options)
            label_match = _wrapped_label_match(logical_row, label_options)
            if label_match is None:
                continue
            compact_row = re.sub(r"\s+", "", logical_row)
            matched_label = next(
                (label for label in label_options if re.sub(r"\s+", "", label) in compact_row),
                label_options[0],
            )
            suffix = logical_row[label_match.end() :]
            cells = _ordered_numeric_or_dash_cells(suffix)
            note_reference: str | None = None
            amount_cells: list[tuple[int, str, str]] | None = None
            layout_state = "ROW_PRESENT_LAYOUT_AMBIGUOUS"

            if has_note_column:
                if (
                    len(cells) == 3
                    and cells[0][1] == "NUMERIC"
                    and re.fullmatch(r"\d{1,4}", cells[0][2].strip()) is not None
                ):
                    note_reference = cells[0][2].strip()
                    amount_cells = cells[1:]
                    layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
                elif (
                    len(cells) == 2
                    and cells[0][1] == "NUMERIC"
                    and re.fullmatch(r"\d{1,4}", cells[0][2].strip()) is None
                ):
                    amount_cells = cells
                    layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_BLANK"
            else:
                if len(cells) == 2:
                    amount_cells = cells
                    layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN"

            current_kind = None
            current_token = None
            current_value_cny = None
            prior_kind = None
            prior_token = None
            if amount_cells is not None and len(amount_cells) == 2:
                current_kind = amount_cells[0][1]
                current_token = amount_cells[0][2]
                prior_kind = amount_cells[1][1]
                prior_token = amount_cells[1][2]
                if current_kind == "NUMERIC":
                    try:
                        parsed = _parse_numeric_token(current_token)
                    except ValueError:
                        layout_state = "ROW_PRESENT_CURRENT_NUMERIC_TOKEN_INVALID"
                    else:
                        if pd.notna(parsed):
                            current_value_cny = float(parsed) * float(scale)
            return {
                "source_row_label": matched_label,
                "statement_unit": str(unit),
                "statement_has_note_column": bool(has_note_column),
                "note_reference": note_reference,
                "row_layout_state": layout_state,
                "current_cell_kind": current_kind,
                "current_cell_token": current_token,
                "current_value_cny": current_value_cny,
                "prior_cell_kind": prior_kind,
                "prior_cell_token": prior_token,
                "source_row_sha256": hashlib.sha256(logical_row.encode("utf-8")).hexdigest(),
                "zero_interpretation_applied": False,
                "private_classification_applied": False,
            }
    return None


def extract_accounting_balance_sheet_row_evidence(text: str) -> dict[str, dict[str, Any]]:
    lines = _normalize_text_lines(text)
    evidence: dict[str, dict[str, Any]] = {}
    for fact_type, labels in ACCOUNTING_BALANCE_SHEET_FACT_LABELS.items():
        row = _direct_consolidated_balance_sheet_row_evidence(lines, labels)
        if row is not None:
            evidence[fact_type] = row
    if not evidence:
        raise ValueError(
            "official filing has no consolidated balance-sheet row evidence "
            "with locally proven CNY units"
        )
    return evidence


def build_accounting_balance_sheet_row_evidence_rows(
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
    evidence = extract_accounting_balance_sheet_row_evidence(text)
    publication = pd.Timestamp(pd.to_datetime(publication_timestamp, errors="raise"))
    rows: list[dict[str, object]] = []
    for fact_type, row in sorted(evidence.items()):
        rows.append(
            {
                "entity_id": str(entity_id),
                "period_end": period_end,
                "fact_type": fact_type,
                "value": row["current_value_cny"],
                "unit": "CNY",
                "evidence_available_date": pd.Timestamp(evidence_available_date).normalize(),
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION,
                **row,
            }
        )
    return pd.DataFrame(rows)


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
    "ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION",
    "build_accounting_balance_sheet_fact_rows",
    "build_accounting_balance_sheet_row_evidence_rows",
    "extract_accounting_balance_sheet_facts",
    "extract_accounting_balance_sheet_row_evidence",
]
