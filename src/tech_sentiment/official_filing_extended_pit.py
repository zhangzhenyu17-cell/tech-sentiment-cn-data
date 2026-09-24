from __future__ import annotations

import re
from typing import Iterable, Mapping

import pandas as pd

from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _AMOUNT_UNIT_SCALE,
    _NUMERIC_TOKEN_RE,
    _explicit_unit_from_text,
    _has_explicit_unit_declaration,
    _logical_row_window,
    _nearest_explicit_unit,
    _normalize_text_lines,
    _parse_numeric_token,
    _wrapped_label_match,
    filing_period_end_from_title,
)


EXTENDED_FILING_PARSER_VERSION = (
    "official-filing-extended-pit-primitives-v4-statement-unit-column-safe-historical-labels"
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
    """Return an explicit unit declared in the header of one statement block."""

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


def _statement_header_has_note_column(
    lines: list[str],
    *,
    start: int,
    end: int,
    max_header_lines: int = 12,
) -> bool:
    """Read an explicit 附注 column declaration only from the statement header."""

    header_end = min(end, start + max_header_lines)
    return any("附注" in line for line in lines[start:header_end])


def _nearby_header_has_note_column(
    lines: list[str],
    index: int,
    *,
    lookback: int = 24,
) -> bool:
    """Return whether the current statement header explicitly declares 附注."""

    left = max(0, index - lookback)
    statement_left = left
    for position in range(index, left - 1, -1):
        if any(
            marker in lines[position]
            for marker in _FINANCIAL_STATEMENT_TOKENS
        ):
            statement_left = position
            break
    return any("附注" in line for line in lines[statement_left : index + 1])


_ROW_ORDINAL_PREFIX_RE = re.compile(
    r"^(?:[一二三四五六七八九十百]+[、.．]|[（(][一二三四五六七八九十百]+[）)])"
)


def _physical_line_starts_label(line: str, labels: Iterable[str]) -> bool:
    """Require the target label to own the current physical PDF line.

    A narrowly defined Chinese accounting row ordinal such as 六、 may
    precede the label. Arbitrary textual prefixes are never stripped.
    """

    compact = re.sub(r"\s+", "", str(line))
    compact = _ROW_ORDINAL_PREFIX_RE.sub("", compact, count=1)
    first_numeric = _NUMERIC_TOKEN_RE.search(compact)
    prefix = compact[: first_numeric.start()] if first_numeric else compact
    if not prefix:
        return False
    return any(
        str(label).startswith(prefix) or prefix.startswith(str(label))
        for label in labels
    )

def _direct_amount_value_after_label(
    lines: list[str],
    labels: Iterable[str],
    *,
    unit_override: str | None = None,
    note_column_override: bool | None = None,
) -> float | None:
    """Extract a direct amount only when the numeric column position is provable.

    The target row must own its label. A source-declared amount unit must be
    available either locally or from the exact statement header. Rows without an
    explicit note column must expose exactly current/prior amount cells. Rows
    with an explicit note column must expose exactly note/current/prior numeric
    cells, and the note token must be a compact integer reference. Ambiguous
    layouts remain missing.
    """

    label_options = tuple(str(label) for label in labels)
    for index in range(len(lines)):
        if not _physical_line_starts_label(lines[index], label_options):
            continue

        logical_row = _logical_row_window(lines, index)
        label_match = _wrapped_label_match(logical_row, label_options)
        if label_match is None:
            continue

        unit = (
            unit_override
            if unit_override is not None
            else _nearest_explicit_unit(lines, index)
        )
        scale = _AMOUNT_UNIT_SCALE.get(str(unit or ""))
        if scale is None:
            continue

        suffix = logical_row[label_match.end() :]
        tokens = list(_NUMERIC_TOKEN_RE.finditer(suffix))
        if not tokens:
            continue

        has_note_column = (
            note_column_override
            if note_column_override is not None
            else _nearby_header_has_note_column(lines, index)
        )
        if has_note_column:
            if len(tokens) != 3:
                continue
            note_token = tokens[0].group(0).strip()
            if re.fullmatch(r"\d{1,4}", note_token) is None:
                continue
            chosen = tokens[1].group(0)
        else:
            if len(tokens) != 2:
                continue
            chosen = tokens[0].group(0)

        try:
            value = _parse_numeric_token(chosen)
        except ValueError:
            continue
        if pd.notna(value):
            return float(value) * scale
    return None


def _direct_amount_value_in_statement_scope(
    lines: list[str],
    labels: Iterable[str],
    *,
    statement_token: str,
) -> float | None:
    """Use statement header metadata without weakening row/column safety."""

    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != statement_token:
            continue
        end = (
            boundaries[offset + 1][0]
            if offset + 1 < len(boundaries)
            else len(lines)
        )
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


def extract_extended_filing_facts(text: str) -> dict[str, float]:
    """Extract direct CNY statement primitives without semantic aggregation.

    Statement-scoped header metadata may extend an explicit unit to long tables,
    but numeric-column ownership remains fail-closed. Missing fields stay
    missing. Debt components remain separate raw facts; this function never
    manufactures a model-facing DEBT value.
    """

    lines = _normalize_text_lines(text)
    if not _has_explicit_unit_declaration(lines):
        raise ValueError(
            "extended filing text does not contain an explicit table unit declaration"
        )

    facts: dict[str, float] = {}
    for fact_type, labels in EXTENDED_AMOUNT_FACT_LABELS.items():
        value = _direct_amount_value_in_statement_scope(
            lines,
            labels,
            statement_token=_FACT_STATEMENT_TOKEN[fact_type],
        )
        if value is None:
            value = _direct_amount_value_after_label(lines, labels)
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
