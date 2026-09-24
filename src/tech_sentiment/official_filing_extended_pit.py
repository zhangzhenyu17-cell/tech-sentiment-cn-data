from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _explicit_unit_from_text,
    _first_amount_value_after_label,
    _has_explicit_unit_declaration,
    _normalize_text_lines,
    filing_period_end_from_title,
)


EXTENDED_FILING_PARSER_VERSION = (
    "official-filing-extended-pit-primitives-v3-historical-capex-labels"
)

# These are direct statement line-items only. They are intentionally not mapped
# to a private model axis and do not create a synthetic aggregate such as DEBT.
EXTENDED_AMOUNT_FACT_LABELS: Mapping[str, tuple[str, ...]] = {
    "MONETARY_FUNDS": ("货币资金",),
    "CASH_AND_CASH_EQUIVALENTS_END": (
        "期末现金及现金等价物余额",
        "现金及现金等价物期末余额",
    ),
    "CAPEX_CASH_PAID": (
        "购建固定资产、无形资产和其他长期资产支付的现金",
        "购建固定资产无形资产和其他长期资产支付的现金",
        "购建固定资产、无形资产和其他长期资产所支付的现金",
        "购建固定资产无形资产和其他长期资产所支付的现金",
    ),
    "R_AND_D_EXPENSE": ("研发费用",),
    "SHORT_TERM_BORROWINGS": ("短期借款",),
    "LONG_TERM_BORROWINGS": ("长期借款",),
    "BONDS_PAYABLE": ("应付债券",),
    "LEASE_LIABILITIES": ("租赁负债",),
    "CURRENT_PORTION_NON_CURRENT_LIABILITIES": ("一年内到期的非流动负债",),
}

_FACT_STATEMENT_TOKEN: Mapping[str, str] = {
    "MONETARY_FUNDS": "资产负债表",
    "SHORT_TERM_BORROWINGS": "资产负债表",
    "CURRENT_PORTION_NON_CURRENT_LIABILITIES": "资产负债表",
    "LONG_TERM_BORROWINGS": "资产负债表",
    "BONDS_PAYABLE": "资产负债表",
    "LEASE_LIABILITIES": "资产负债表",
    "R_AND_D_EXPENSE": "利润表",
    "CAPEX_CASH_PAID": "现金流量表",
    "CASH_AND_CASH_EQUIVALENTS_END": "现金流量表",
}
_FINANCIAL_STATEMENT_TOKENS = (
    "资产负债表",
    "利润表",
    "现金流量表",
    "所有者权益变动表",
)


def _compact_line(value: object) -> str:
    return "".join(str(value).split())


def _statement_boundaries(lines: list[str]) -> list[tuple[int, str]]:
    boundaries: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        compact = _compact_line(line)
        for token in _FINANCIAL_STATEMENT_TOKENS:
            if token in compact:
                boundaries.append((index, token))
                break
    return boundaries


def _explicit_statement_unit(
    lines: list[str],
    *,
    start: int,
    end: int,
    max_header_lines: int = 12,
    max_unit_lines: int = 5,
) -> str | None:
    """Return an explicit unit declared in the header of one statement block.

    This is a bounded source-metadata lookup, not unit inference. The search is
    limited to the current financial-statement block and its leading header
    region so a unit from a previous or different statement can never leak in.
    """

    header_end = min(end, start + max_header_lines)
    for position in range(start, header_end):
        for span in range(1, max_unit_lines + 1):
            candidate_end = position + span
            if candidate_end > header_end:
                break
            unit = _explicit_unit_from_text(" ".join(lines[position:candidate_end]))
            if unit is not None:
                return unit
    return None


def _first_amount_value_in_statement_scope(
    lines: list[str],
    labels: Iterable[str],
    *,
    statement_token: str,
) -> float | None:
    """Recover a direct line-item using only its statement's explicit unit.

    The legacy amount helper intentionally uses a short backward unit window.
    Long cash-flow and balance-sheet tables can place a valid row dozens of text
    lines after the explicit ``单位`` header. For those cases we identify an
    exact statement block, require its own explicit unit, and present that unit
    beside a short row-local window to the existing value parser. No unit is
    guessed, no document ordering is inferred, and no private model aggregate is
    created.
    """

    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != statement_token:
            continue
        end = boundaries[offset + 1][0] if offset + 1 < len(boundaries) else len(lines)
        unit = _explicit_statement_unit(lines, start=start, end=end)
        if unit is None:
            continue
        synthetic_unit_line = f"单位：人民币{unit}"
        for index in range(start, end):
            row_end = min(end, index + 6)
            scoped_lines = [synthetic_unit_line, *lines[index:row_end]]
            value = _first_amount_value_after_label(scoped_lines, labels)
            if value is not None:
                return float(value)
    return None


def extract_extended_filing_facts(text: str) -> dict[str, float]:
    """Extract direct CNY statement primitives without semantic aggregation.

    Every value must be supported by an explicit table-unit contract. For facts
    with a known financial-statement home, the parser first selects within that
    statement block so a later parent-company table cannot outrank the earlier
    consolidated statement merely because its row is closer to a unit header.
    The legacy short-range extractor remains only as a compatibility fallback
    for historical layouts without recognizable statement boundaries. Missing
    fields stay missing. Debt components remain separate raw facts; this
    function never manufactures a model-facing DEBT value.
    """

    lines = _normalize_text_lines(text)
    if not _has_explicit_unit_declaration(lines):
        raise ValueError(
            "extended filing text does not contain an explicit table unit declaration"
        )

    facts: dict[str, float] = {}
    for fact_type, labels in EXTENDED_AMOUNT_FACT_LABELS.items():
        statement_token = _FACT_STATEMENT_TOKEN[fact_type]
        value = _first_amount_value_in_statement_scope(
            lines,
            labels,
            statement_token=statement_token,
        )
        if value is None:
            value = _first_amount_value_after_label(lines, labels)
        if value is not None:
            facts[fact_type] = float(value)

    if not facts:
        raise ValueError(
            "official filing has no extended PIT primitives with locally proven CNY units"
        )
    return facts


def build_extended_filing_fact_rows(
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
    """Build provenance-rich rows for extended raw filing primitives."""

    period_end = filing_period_end_from_title(title)
    facts = extract_extended_filing_facts(text)
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
                "parser_version": EXTENDED_FILING_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS))


__all__ = [
    "EXTENDED_AMOUNT_FACT_LABELS",
    "EXTENDED_FILING_PARSER_VERSION",
    "build_extended_filing_fact_rows",
    "extract_extended_filing_facts",
]
