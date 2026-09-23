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
    "official-filing-extended-pit-primitives-v3-column-safe-statement-unit-scope"
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
    ),
    "R_AND_D_EXPENSE": ("研发费用",),
    "SHORT_TERM_BORROWINGS": ("短期借款",),
    "LONG_TERM_BORROWINGS": ("长期借款",),
    "BONDS_PAYABLE": ("应付债券",),
    "LEASE_LIABILITIES": ("租赁负债",),
    "CURRENT_PORTION_NON_CURRENT_LIABILITIES": ("一年内到期的非流动负债",),
}

_STATEMENT_BOUNDARY_MARKERS = (
    "资产负债表",
    "利润表",
    "现金流量表",
    "所有者权益变动表",
)
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


def _nearby_header_has_note_column(
    lines: list[str],
    index: int,
    *,
    lookback: int = 24,
) -> bool:
    """Return whether the current statement header explicitly declares 附注.

    The search is bounded to the nearest financial-statement boundary so an
    ``附注`` header in a preceding balance sheet cannot contaminate a following
    income statement or cash-flow statement. A false positive only causes the
    extended parser to fail closed for the row; it never changes an extracted
    amount.
    """

    left = max(0, index - lookback)
    statement_left = left
    for position in range(index, left - 1, -1):
        if any(marker in lines[position] for marker in _STATEMENT_BOUNDARY_MARKERS):
            statement_left = position
            break
    return any("附注" in line for line in lines[statement_left : index + 1])


def _physical_line_starts_label(line: str, labels: Iterable[str]) -> bool:
    """Require the target row label to begin on the current physical PDF line.

    ``_logical_row_window`` joins forward so a visually wrapped label can be
    reconstructed. Without this guard, starting from an unrelated table-header
    line can also absorb the following row and create a synthetic match. The
    current physical line must therefore already begin the target label, or a
    non-numeric prefix of a wrapped target label. This keeps legitimate wrapped
    labels while preventing a preceding header/row from owning the match.
    """

    compact = re.sub(r"\s+", "", str(line))
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
    explicit_unit: str | None = None,
    explicit_note_column: bool | None = None,
) -> float | None:
    """Extract a direct amount only when the numeric column position is provable.

    The generic filing parser historically accepts the first numeric token after
    a label. That is unsafe for extended raw primitives because many Chinese
    statement tables insert an explicit ``附注`` column before the current-period
    amount. In that layout a row such as ``货币资金 七、1 12,345 11,111`` would
    otherwise emit ``1`` as the monetary-funds value.

    ``explicit_unit`` and ``explicit_note_column`` are source metadata proven
    from the current statement header. They are never inferred. When omitted,
    the legacy bounded row-local checks remain in force for backward-compatible
    historical layouts without recognizable statement boundaries.
    """

    label_options = tuple(str(label) for label in labels)
    for index in range(len(lines)):
        if not _physical_line_starts_label(lines[index], label_options):
            continue

        logical_row = _logical_row_window(lines, index)
        label_match = _wrapped_label_match(logical_row, label_options)
        if label_match is None:
            # Do not use fragmented-label recovery here. If a numeric cell
            # interrupts the visible label, the column position is ambiguous.
            continue

        unit = (
            explicit_unit
            if explicit_unit is not None
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
            explicit_note_column
            if explicit_note_column is not None
            else _nearby_header_has_note_column(lines, index)
        )
        if has_note_column:
            # A proven note-column layout is safe only when the row itself has a
            # compact note-reference token followed by two statement amounts.
            # Blank-note rows are intentionally left missing because two numeric
            # cells alone cannot distinguish current/prior amounts from
            # note/current amounts after PDF layout collapse.
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


def _statement_boundaries(lines: list[str]) -> list[tuple[int, str]]:
    boundaries: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", str(line))
        for token in _STATEMENT_BOUNDARY_MARKERS:
            if token in compact:
                boundaries.append((index, token))
                break
    return boundaries


def _statement_header_metadata(
    block: list[str],
    *,
    max_header_lines: int = 12,
    max_unit_lines: int = 5,
) -> tuple[str | None, bool]:
    """Read only explicit unit/note metadata from one statement's header.

    The search is limited to the leading lines of a single statement block. It
    cannot inherit a unit or ``附注`` column declaration from a preceding or
    following statement.
    """

    header = block[:max_header_lines]
    unit: str | None = None
    for position in range(len(header)):
        for span in range(1, max_unit_lines + 1):
            end = position + span
            if end > len(header):
                break
            unit = _explicit_unit_from_text(" ".join(header[position:end]))
            if unit is not None:
                break
        if unit is not None:
            break
    return unit, any("附注" in line for line in header)


def _direct_amount_value_in_statement_scope(
    lines: list[str],
    labels: Iterable[str],
    *,
    statement_token: str,
) -> float | None:
    """Prefer the first explicit matching financial statement in the filing.

    This extends the unit/header scope across long statements while retaining
    the v2 column-safety rules. The first matching statement block is evaluated
    before later parent-company statements, so a nearby parent row cannot
    outrank a farther consolidated row. A block without its own explicit unit is
    skipped rather than borrowing metadata from another statement.
    """

    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != statement_token:
            continue
        end = boundaries[offset + 1][0] if offset + 1 < len(boundaries) else len(lines)
        block = lines[start:end]
        unit, has_note_column = _statement_header_metadata(block)
        if unit is None:
            continue
        value = _direct_amount_value_after_label(
            block,
            labels,
            explicit_unit=unit,
            explicit_note_column=has_note_column,
        )
        if value is not None:
            return float(value)
    return None


def extract_extended_filing_facts(text: str) -> dict[str, float]:
    """Extract direct CNY statement primitives without semantic aggregation.

    Every value must have an explicit CNY unit and a provable numeric-column
    position. Known statement line-items first use the corresponding statement's
    own header metadata, which safely supports long tables. The prior bounded
    row-local parser remains a fallback for historical layouts whose statement
    boundaries cannot be recognized. Missing fields stay missing. Debt
    components remain separate raw facts; this function never manufactures a
    model-facing DEBT value.
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
