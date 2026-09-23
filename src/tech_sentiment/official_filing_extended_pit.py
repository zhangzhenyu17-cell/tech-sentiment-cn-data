from __future__ import annotations

import re
from typing import Iterable, Mapping

import pandas as pd

from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _AMOUNT_UNIT_SCALE,
    _NUMERIC_TOKEN_RE,
    _has_explicit_unit_declaration,
    _logical_row_window,
    _nearest_explicit_unit,
    _normalize_text_lines,
    _parse_numeric_token,
    _wrapped_label_match,
    filing_period_end_from_title,
)


EXTENDED_FILING_PARSER_VERSION = "official-filing-extended-pit-primitives-v2-column-safe"

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
) -> float | None:
    """Extract a direct amount only when the numeric column position is provable.

    The generic filing parser historically accepts the first numeric token after
    a label. That is unsafe for extended raw primitives because many Chinese
    statement tables insert an explicit ``附注`` column before the current-period
    amount. In that layout a row such as ``货币资金 七、1 12,345 11,111`` would
    otherwise emit ``1`` as the monetary-funds value.

    Rules here are intentionally conservative:
    * the target label must begin on the current physical PDF line, while a
      non-numeric wrapped-label prefix may continue onto later physical lines;
    * a local explicit CNY amount unit must be present;
    * without an explicit nearby ``附注`` header, exactly two numeric amount
      cells must be visible and the first is the current-period value;
    * with an explicit ``附注`` header, exactly a syntactic note-reference token
      plus two amount cells must be present; the first amount after the note
      reference is used;
    * one-token rows are rejected because adjacent current/prior amount cells can
      collapse into one numeric token in PDF text extraction;
    * layouts that cannot prove the amount-column position remain missing.

    No financial magnitude threshold, imputation, model mapping, or cross-row
    inference is used.
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

        unit = _nearest_explicit_unit(lines, index)
        scale = _AMOUNT_UNIT_SCALE.get(str(unit or ""))
        if scale is None:
            continue

        suffix = logical_row[label_match.end() :]
        tokens = list(_NUMERIC_TOKEN_RE.finditer(suffix))
        if not tokens:
            continue

        if _nearby_header_has_note_column(lines, index):
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


def extract_extended_filing_facts(text: str) -> dict[str, float]:
    """Extract direct CNY statement primitives without semantic aggregation.

    Every value must be supported by a bounded, explicit table-unit contract and
    an unambiguous numeric-column position. Missing fields stay missing. Debt
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
