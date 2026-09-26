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
    "official-filing-extended-pit-primitives-v8-statement-dash-cell-safe"
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

# V9 does not reinterpret a blank/dash cell as numeric zero.  It adds one
# separate evidence route: a blank non-current-liability debt row may be
# reconciled to zero only when the exact consolidated balance-sheet subtotal is
# reproduced by every explicit standardized component in the same CNY scope.
# The component set is deliberately closed and all component balances are
# treated as non-negative liability presentation amounts; any negative,
# absent, ambiguous, non-CNY, or non-zero residual fails closed.
_NON_CURRENT_LIABILITY_COMPONENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("INSURANCE_CONTRACT_RESERVE", ("保险合同准备金",)),
    ("LONG_TERM_BORROWINGS", ("长期借款",)),
    ("BONDS_PAYABLE", ("应付债券",)),
    ("LEASE_LIABILITIES", ("租赁负债",)),
    ("LONG_TERM_PAYABLES", ("长期应付款",)),
    ("LONG_TERM_EMPLOYEE_BENEFITS", ("长期应付职工薪酬",)),
    ("PROVISIONS", ("预计负债",)),
    ("DEFERRED_INCOME", ("递延收益",)),
    ("DEFERRED_TAX_LIABILITIES", ("递延所得税负债",)),
    ("OTHER_NON_CURRENT_LIABILITIES", ("其他非流动负债",)),
)
_RECONCILABLE_NON_CURRENT_DEBT_FACTS = frozenset(
    {"LONG_TERM_BORROWINGS", "BONDS_PAYABLE", "LEASE_LIABILITIES"}
)
_NON_CURRENT_LIABILITY_SUBTOTAL_LABELS = ("非流动负债合计",)


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
_NOTE_COLUMN_TAIL_RE = re.compile(
    r"^(?P<label_prefix>.+?)(?P<note>[一二三四五六七八九十百]+、\d{1,4})$"
)
_STANDALONE_DASH_CELL_RE = re.compile(r"(?<!\S)-(?!\S)")


def _ordered_numeric_or_dash_cells(text: str) -> list[tuple[int, str, str]]:
    """Return ordered numeric/dash cells without assigning semantics to dash.

    A standalone dash is only a column placeholder. It is never parsed as zero
    and is never returned as a model/input value. This helper exists solely so
    statement-scoped extraction can prove current/prior column ownership when
    one amount cell is explicitly blank as a dash.
    """

    cells = [
        (match.start(), "NUMERIC", match.group(0))
        for match in _NUMERIC_TOKEN_RE.finditer(text)
    ]
    cells.extend(
        (match.start(), "DASH", match.group(0))
        for match in _STANDALONE_DASH_CELL_RE.finditer(text)
    )
    return sorted(cells, key=lambda item: item[0])


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

def _wrapped_label_note_then_amount_value(
    lines: list[str],
    index: int,
    labels: Iterable[str],
    *,
    unit_override: str | None = None,
    note_column_override: bool | None = None,
    max_continuation_lines: int = 2,
) -> float | None:
    """Recover a wrapped statement label split around an explicit note reference.

    Some pypdf layout rows place an incomplete label plus the note reference on
    one physical line, then emit the final label glyphs together with the
    current/prior amount cells on the next line.  Recovery is allowed only when
    the statement header explicitly declares an 附注 column, the first line
    ends in a Chinese note reference such as 七、43, the label continuation is
    exact, and the continuation row owns exactly current/prior amount cells.
    """

    if note_column_override is not True:
        return None
    compact = re.sub(r"\s+", "", str(lines[index]))
    match = _NOTE_COLUMN_TAIL_RE.fullmatch(compact)
    if match is None:
        return None

    prefix = match.group("label_prefix")
    candidates = [
        str(label)
        for label in labels
        if str(label).startswith(prefix) and str(label) != prefix
    ]
    if not candidates:
        return None

    continuation = ""
    for position in range(
        index + 1,
        min(len(lines), index + 1 + max_continuation_lines),
    ):
        physical = str(lines[position])
        tokens = list(_NUMERIC_TOKEN_RE.finditer(physical))
        text_prefix = re.sub(
            r"\s+", "", physical[: tokens[0].start()] if tokens else physical
        )
        if not text_prefix:
            break
        continuation += text_prefix
        viable = [
            label
            for label in candidates
            if label.startswith(prefix + continuation)
        ]
        if not viable:
            break
        exact = [
            label
            for label in viable
            if label == prefix + continuation
        ]
        if exact:
            if len(exact) != 1 or len(tokens) != 2:
                return None
            unit = (
                unit_override
                if unit_override is not None
                else _nearest_explicit_unit(lines, index)
            )
            scale = _AMOUNT_UNIT_SCALE.get(str(unit or ""))
            if scale is None:
                return None
            try:
                value = _parse_numeric_token(tokens[0].group(0))
            except ValueError:
                return None
            if pd.notna(value):
                return float(value) * scale
            return None
        if tokens:
            break

    return None


def _target_logical_row_window(
    lines: list[str],
    index: int,
    labels: Iterable[str],
    *,
    max_lines: int = 6,
) -> str:
    """Rejoin only an incomplete wrapped target label, never a completed blank row.

    Once an exact target label is already present in the accumulated physical
    row and no numeric cell is present, the row is treated as blank. This
    prevents a blank statement item such as 应付债券 from borrowing numeric
    cells from the following 租赁负债 row.
    """

    label_options = tuple(str(label) for label in labels)
    parts: list[str] = []
    for position in range(index, min(len(lines), index + max_lines)):
        if parts:
            joined_before = " ".join(parts)
            if _wrapped_label_match(joined_before, label_options) is not None:
                break
        parts.append(lines[position])
        joined = " ".join(parts)
        if _NUMERIC_TOKEN_RE.search(joined):
            break
    return " ".join(parts)


def _tail_fragment_amount_value(
    lines: list[str],
    index: int,
    labels: Iterable[str],
    *,
    unit_override: str | None = None,
    note_column_override: bool | None = None,
    max_continuation_lines: int = 2,
) -> float | None:
    """Recover an exact target label whose text tail follows its amount cells.

    Some PDF text layers emit a table row as a strict label prefix plus exactly
    current/prior amounts, then place the final label glyphs on a following
    text-only line. Complete labels, fuzzy continuations, and numeric
    continuations are never accepted. In statements with an 附注 column, the
    first numeric token must be amount-like rather than a 1-4 digit note id.
    """

    physical = str(lines[index])
    tokens = list(_NUMERIC_TOKEN_RE.finditer(physical))
    if len(tokens) != 2:
        return None

    has_note_column = (
        note_column_override
        if note_column_override is not None
        else _nearby_header_has_note_column(lines, index)
    )
    if has_note_column and re.fullmatch(r"\d{1,4}", tokens[0].group(0).strip()):
        return None

    prefix = re.sub(r"\s+", "", physical[: tokens[0].start()])
    prefix = _ROW_ORDINAL_PREFIX_RE.sub("", prefix, count=1)
    if not prefix:
        return None

    candidates = [
        str(label)
        for label in labels
        if str(label).startswith(prefix) and str(label) != prefix
    ]
    if not candidates:
        return None

    continuation = ""
    for position in range(
        index + 1,
        min(len(lines), index + 1 + max_continuation_lines),
    ):
        compact = re.sub(r"\s+", "", str(lines[position]))
        if not compact or _NUMERIC_TOKEN_RE.search(compact):
            break
        continuation += compact
        exact = [label for label in candidates if prefix + continuation == label]
        if len(exact) == 1:
            unit = (
                unit_override
                if unit_override is not None
                else _nearest_explicit_unit(lines, index)
            )
            scale = _AMOUNT_UNIT_SCALE.get(str(unit or ""))
            if scale is None:
                return None
            try:
                value = _parse_numeric_token(tokens[0].group(0))
            except ValueError:
                return None
            if pd.notna(value):
                return float(value) * scale
            return None
        if not any(label.startswith(prefix + continuation) for label in candidates):
            break

    return None


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
            wrapped_note_value = _wrapped_label_note_then_amount_value(
                lines,
                index,
                label_options,
                unit_override=unit_override,
                note_column_override=note_column_override,
            )
            if wrapped_note_value is not None:
                return float(wrapped_note_value)
            continue

        logical_row = _target_logical_row_window(lines, index, label_options)
        label_match = _wrapped_label_match(logical_row, label_options)
        if label_match is None:
            fragmented = _tail_fragment_amount_value(
                lines,
                index,
                label_options,
                unit_override=unit_override,
                note_column_override=note_column_override,
            )
            if fragmented is not None:
                return float(fragmented)
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
        statement_scoped = (
            unit_override is not None and note_column_override is not None
        )
        cells = (
            _ordered_numeric_or_dash_cells(suffix)
            if statement_scoped
            else []
        )

        has_note_column = (
            note_column_override
            if note_column_override is not None
            else _nearby_header_has_note_column(lines, index)
        )
        chosen: str | None = None
        if has_note_column:
            if len(tokens) == 3:
                note_token = tokens[0].group(0).strip()
                if re.fullmatch(r"\d{1,4}", note_token) is None:
                    continue
                chosen = tokens[1].group(0)
            elif (
                statement_scoped
                and any(kind == "DASH" for _, kind, _ in cells)
            ):
                if len(cells) == 3:
                    _, note_kind, note_text = cells[0]
                    if (
                        note_kind != "NUMERIC"
                        or re.fullmatch(r"\d{1,4}", note_text.strip()) is None
                    ):
                        continue
                    amount_cells = cells[1:]
                elif len(cells) == 2:
                    _, first_kind, first_text = cells[0]
                    if (
                        first_kind != "NUMERIC"
                        or re.fullmatch(r"\d{1,4}", first_text.strip())
                    ):
                        continue
                    amount_cells = cells
                else:
                    continue
                if (
                    len(amount_cells) != 2
                    or amount_cells[0][1] != "NUMERIC"
                ):
                    continue
                chosen = amount_cells[0][2]
            else:
                continue
        else:
            if len(tokens) == 2:
                chosen = tokens[0].group(0)
            elif (
                statement_scoped
                and any(kind == "DASH" for _, kind, _ in cells)
            ):
                if len(cells) != 2 or cells[0][1] != "NUMERIC":
                    continue
                chosen = cells[0][2]
            else:
                continue

        if chosen is None:
            continue
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


def _exact_statement_row_current_state(
    lines: list[str],
    labels: Iterable[str],
    *,
    unit_override: str,
) -> tuple[str, float | None]:
    """Return NUMERIC/BLANK/AMBIGUOUS/ABSENT for one no-note statement row.

    This helper is intentionally narrower than the ordinary parser.  It is used
    only by subtotal reconciliation, where a zero may be inferred only from a
    closed accounting identity.  A single unowned numeric cell, a wrapped row,
    or any other uncertain layout is AMBIGUOUS rather than guessed.
    """

    label_options = tuple(str(label) for label in labels)
    scale = _AMOUNT_UNIT_SCALE.get(str(unit_override or ""))
    if scale != 1.0:
        return ("AMBIGUOUS", None)

    found = False
    for index in range(len(lines)):
        if not _physical_line_starts_label(lines[index], label_options):
            continue
        found = True
        logical_row = _target_logical_row_window(lines, index, label_options)
        label_match = _wrapped_label_match(logical_row, label_options)
        if label_match is None:
            return ("AMBIGUOUS", None)
        suffix = logical_row[label_match.end() :]
        cells = _ordered_numeric_or_dash_cells(suffix)
        if not cells:
            return ("BLANK", None)
        if len(cells) != 2:
            return ("AMBIGUOUS", None)
        _, current_kind, current_text = cells[0]
        if current_kind == "DASH":
            return ("BLANK", None)
        if current_kind != "NUMERIC":
            return ("AMBIGUOUS", None)
        try:
            value = _parse_numeric_token(current_text)
        except ValueError:
            return ("AMBIGUOUS", None)
        if pd.isna(value):
            return ("AMBIGUOUS", None)
        return ("NUMERIC", float(value))

    return ("ABSENT" if not found else "AMBIGUOUS", None)


def _subtotal_reconciled_zero_non_current_debt(
    lines: list[str],
) -> dict[str, float]:
    """Prove selected blank debt rows as zero from a closed liability subtotal.

    This is not blank-to-zero imputation.  The route is allowed only for the
    consolidated balance sheet, explicit CNY unit, no note column, the complete
    standardized non-current-liability row set, non-negative explicit balances,
    and a current-period subtotal residual within half a cent of exactly zero.
    Under those constraints, every blank component in the non-negative closed
    set is mathematically zero.  Only the three frozen debt components are
    emitted; no synthetic aggregate is created.
    """

    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != "资产负债表":
            continue
        if "合并资产负债表" not in _compact_line(lines[start]):
            continue
        end = (
            boundaries[offset + 1][0]
            if offset + 1 < len(boundaries)
            else len(lines)
        )
        statement = lines[start:end]
        unit = _explicit_statement_unit(statement, start=0, end=len(statement))
        if unit is None or _AMOUNT_UNIT_SCALE.get(str(unit)) != 1.0:
            continue
        if _statement_header_has_note_column(statement, start=0, end=len(statement)):
            continue

        non_current_start = None
        subtotal_index = None
        for index, line in enumerate(statement):
            compact = _compact_line(line)
            if compact in {"非流动负债：", "非流动负债:"}:
                non_current_start = index
                continue
            if non_current_start is not None and compact.startswith("非流动负债合计"):
                subtotal_index = index
                break
        if non_current_start is None or subtotal_index is None:
            continue
        scope = statement[non_current_start + 1 : subtotal_index + 1]

        subtotal = _direct_amount_value_after_label(
            scope,
            _NON_CURRENT_LIABILITY_SUBTOTAL_LABELS,
            unit_override=unit,
            note_column_override=False,
        )
        if subtotal is None or subtotal < 0:
            continue

        explicit_sum = 0.0
        blank_facts: list[str] = []
        failed = False
        for fact_type, labels in _NON_CURRENT_LIABILITY_COMPONENTS:
            state, value = _exact_statement_row_current_state(
                scope,
                labels,
                unit_override=unit,
            )
            if state == "NUMERIC":
                assert value is not None
                if value < 0:
                    failed = True
                    break
                explicit_sum += float(value)
            elif state == "BLANK":
                if fact_type in _RECONCILABLE_NON_CURRENT_DEBT_FACTS:
                    blank_facts.append(fact_type)
            else:
                failed = True
                break
        if failed or not blank_facts:
            continue

        residual = float(subtotal) - explicit_sum
        if abs(residual) > 0.005:
            continue
        return {fact_type: 0.0 for fact_type in blank_facts}

    return {}


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
    boundaries = _statement_boundaries(lines)
    statement_tokens = {token for _, token in boundaries}
    for fact_type, labels in EXTENDED_AMOUNT_FACT_LABELS.items():
        statement_token = _FACT_STATEMENT_TOKEN[fact_type]
        value = _direct_amount_value_in_statement_scope(
            lines,
            labels,
            statement_token=statement_token,
        )
        # If the exact target statement is present, never fall back to notes,
        # MD&A or risk tables elsewhere in the filing. A blank or ambiguous
        # statement row remains missing.
        if value is None and statement_token not in statement_tokens:
            value = _direct_amount_value_after_label(lines, labels)
        if value is not None:
            facts[fact_type] = float(value)

    # V9 adds only accounting-identity-proven zeros.  Existing direct numeric
    # facts always win; blank/dash semantics above are unchanged.
    for fact_type, value in _subtotal_reconciled_zero_non_current_debt(lines).items():
        facts.setdefault(fact_type, float(value))

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
