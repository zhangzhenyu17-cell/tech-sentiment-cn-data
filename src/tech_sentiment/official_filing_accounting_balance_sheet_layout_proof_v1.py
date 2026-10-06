from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

import pandas as pd

from .official_filing_accounting_balance_sheet_v1 import (
    ACCOUNTING_BALANCE_SHEET_FACT_LABELS,
)
from .official_filing_extended_pit import (
    _compact_line,
    _explicit_statement_unit,
    _ordered_numeric_or_dash_cells,
    _physical_line_starts_label,
    _statement_boundaries,
    _statement_header_has_note_column,
    _target_logical_row_window,
)
from .official_filing_facts import (
    _AMOUNT_UNIT_SCALE,
    _normalize_pdf_unicode,
    _parse_numeric_token,
    _wrapped_label_match,
)


ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION = (
    "official-filing-accounting-balance-sheet-layout-proof-v5-residual-column-safe"
)

_CURRENT_HEADER_TOKENS = (
    "期末余额",
    "期末数",
    "期末金额",
    "本期末余额",
)
_PRIOR_HEADER_TOKENS = (
    "期初余额",
    "期初数",
    "年初余额",
    "年初数",
    "上年年末余额",
    "上期期末余额",
)

_BALANCE_SHEET_DATE_RE = re.compile(r"20\d{2}年\d{1,2}月\d{1,2}日")
_BALANCE_SHEET_SPACED_DATE_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
_BALANCE_SHEET_SPACED_DATE_ANCHOR_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}(?:\s*日)?")
_TEXTUAL_NOTE_REFERENCE_RE = re.compile(
    r"^\s*(?P<note>[一二三四五六七八九十百]+\s*(?:(?:[-－–—、.．]\s*)?(?:[（(]\s*\d{1,4}\s*[）)])+|[-－–—、.．]\s*\d{1,4}))"
)
_PAREN_NOTE_TOKEN_RE = re.compile(r"^[（(]\s*(?P<number>\d{1,4})\s*[）)]$")
_PAREN_NOTE_FIND_RE = re.compile(r"[（(]\s*(?P<number>\d{1,4})\s*[）)]")


def _explicit_textual_note_reference(text: str) -> str | None:
    match = _TEXTUAL_NOTE_REFERENCE_RE.match(str(text))
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group("note"))


def _strip_explicit_textual_note_reference(text: str) -> tuple[str | None, str]:
    match = _TEXTUAL_NOTE_REFERENCE_RE.match(str(text))
    if match is None:
        return None, str(text)
    note = re.sub(r"\s+", "", match.group("note"))
    return note, str(text)[match.end():]


def _is_exact_consolidated_balance_sheet_title(line: str) -> bool:
    compact = _compact_line(line)
    compact = re.sub(r"^(?:\d+|[一二三四五六七八九十百]+)[、.．]", "", compact, count=1)
    return compact == "合并资产负债表"


def _parenthesized_note_token_sequence(note_reference: str | None) -> list[str]:
    if note_reference is None:
        return []
    return [f"({match.group('number')})" for match in _PAREN_NOTE_FIND_RE.finditer(str(note_reference))]


def _normalized_parenthesized_token(token: str) -> str | None:
    match = _PAREN_NOTE_TOKEN_RE.fullmatch(str(token).strip())
    if match is None:
        return None
    return f"({match.group('number')})"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _layout_text_lines(text: str) -> list[str]:
    clean = (
        _normalize_pdf_unicode(text)
        .replace("，", ",")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("／", "/")
        .replace("−", "-")
        .replace("—", "-")
    )
    return [line.rstrip() for line in clean.splitlines() if line.strip()]


def _amount_columns_declared(
    lines: list[str],
    *,
    start: int,
    end: int,
    max_header_lines: int = 12,
) -> bool:
    header_end = min(end, start + max_header_lines)
    compact = "".join(_compact_line(line) for line in lines[start:header_end])
    has_current = any(token in compact for token in _CURRENT_HEADER_TOKENS)
    has_prior = any(token in compact for token in _PRIOR_HEADER_TOKENS)
    if has_current and has_prior:
        return True
    distinct_dates = set(_BALANCE_SHEET_DATE_RE.findall(compact))
    return len(distinct_dates) >= 2


def _statement_column_anchors(
    lines: list[str],
    *,
    start: int,
    end: int,
    max_header_lines: int = 12,
) -> tuple[int, int, int | None] | None:
    header_end = min(end, start + max_header_lines)
    for line in lines[start:header_end]:
        current_pos = next(
            (line.find(token) for token in _CURRENT_HEADER_TOKENS if token in line),
            -1,
        )
        prior_pos = next(
            (line.find(token) for token in _PRIOR_HEADER_TOKENS if token in line),
            -1,
        )
        if current_pos >= 0 and prior_pos > current_pos:
            note_pos = line.find("附注")
            return (current_pos, prior_pos, note_pos if note_pos >= 0 else None)
        dates = list(_BALANCE_SHEET_SPACED_DATE_ANCHOR_RE.finditer(line))
        if len(dates) >= 2:
            current_pos = dates[0].start()
            prior_pos = dates[1].start()
            if prior_pos <= current_pos:
                continue
            note_pos = line.find("附注")
            return (current_pos, prior_pos, note_pos if note_pos >= 0 else None)
    return None


def _assign_cells_by_column_position(
    *,
    cells: list[tuple[int, str, str]],
    anchors: tuple[int, int, int | None],
    has_note_column: bool,
) -> tuple[str | None, list[tuple[int, str, str]]] | None:
    current_anchor, prior_anchor, note_anchor = anchors
    if prior_anchor <= current_anchor:
        return None
    remaining = list(cells)
    note_reference: str | None = None
    # A note column declared elsewhere in the statement header cannot be
    # positionally stripped unless its anchor is present on the same header
    # line that supplied the amount-column anchors.  Otherwise a compact note
    # id (for example 12) could be mistaken for a current-period amount.
    if has_note_column and note_anchor is None:
        return None
    if has_note_column and note_anchor is not None and note_anchor < current_anchor:
        note_boundary = (note_anchor + current_anchor) / 2.0
        note_cells = [cell for cell in remaining if cell[0] < note_boundary]
        if len(note_cells) > 1:
            return None
        if len(note_cells) == 1:
            _, kind, token = note_cells[0]
            if kind != "NUMERIC" or re.fullmatch(r"\d{1,4}", token.strip()) is None:
                return None
            note_reference = token.strip()
            remaining.remove(note_cells[0])

    amount_boundary = (current_anchor + prior_anchor) / 2.0
    current_cells = [cell for cell in remaining if cell[0] < amount_boundary]
    prior_cells = [cell for cell in remaining if cell[0] >= amount_boundary]
    if len(current_cells) > 1 or len(prior_cells) > 1:
        return None
    current = current_cells[0] if current_cells else _blank_cell()
    prior = prior_cells[0] if prior_cells else _blank_cell()
    return note_reference, [current, prior]


def _scaled_numeric(token: str | None, unit: str) -> float | None:
    if token is None:
        return None
    scale = _AMOUNT_UNIT_SCALE.get(str(unit))
    if scale is None:
        return None
    try:
        value = _parse_numeric_token(token)
    except ValueError:
        return None
    if pd.isna(value):
        return None
    return float(value) * float(scale)


def _blank_cell() -> tuple[int, str, str]:
    return (-1, "BLANK", "")


def _large_gap_two_amount_cells(
    line: str,
    labels: Iterable[str],
) -> list[tuple[int, str, str]] | None:
    """Recover two amount cells from PDF glyph-spacing corruption, fail closed.

    Some official PDFs insert 1-2 spaces between digits while retaining very
    large horizontal gaps between the label/current/prior columns. Recovery is
    allowed only for an exact three-chunk physical row (label + two amounts)
    separated by at least eight spaces. Each amount chunk, after whitespace
    removal, must itself be exactly one numeric/dash token.
    """

    chunks = [chunk.strip() for chunk in re.split(r"\s{8,}", str(line)) if chunk.strip()]
    if len(chunks) != 3:
        return None
    label_chunk = _compact_line(chunks[0])
    if not any(label_chunk == _compact_line(label) for label in labels):
        return None
    recovered: list[tuple[int, str, str]] = []
    for position, chunk in enumerate(chunks[1:]):
        compact = re.sub(r"\s+", "", chunk)
        cells = _ordered_numeric_or_dash_cells(compact)
        if len(cells) != 1 or cells[0][2] != compact:
            return None
        recovered.append((position, cells[0][1], cells[0][2]))
    return recovered


def _classify_cells(
    *,
    cells: list[tuple[int, str, str]],
    has_note_column: bool,
    amount_columns_declared: bool,
    textual_note_reference: str | None = None,
) -> tuple[str, str | None, list[tuple[int, str, str]] | None]:
    """Classify raw row cells without treating blank or dash as zero.

    Current/prior ownership is accepted only after the statement header proves
    both amount columns. Textual note references such as 七(1), 七.46, or
    七(4)(71) are stripped only when their exact parenthesized token sequence is
    present at the front of the row cells. Remaining cells must be exactly two
    amount cells; otherwise the row stays unresolved.
    """

    if not amount_columns_declared:
        return ("HEADER_AMOUNT_COLUMNS_NOT_PROVEN", None, None)

    if not has_note_column:
        if len(cells) == 0:
            return (
                "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_BLANK_BOTH",
                None,
                [_blank_cell(), _blank_cell()],
            )
        if len(cells) == 2:
            return ("ROW_PRESENT_AMOUNT_COLUMNS_PROVEN", None, cells)
        return ("ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED", None, None)

    if textual_note_reference is not None:
        amount_cells = list(cells)
        if len(amount_cells) == 0:
            return (
                "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_ONLY_AMOUNTS_BLANK",
                textual_note_reference,
                [_blank_cell(), _blank_cell()],
            )
        if len(amount_cells) == 2:
            return (
                "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE",
                textual_note_reference,
                amount_cells,
            )
        return ("ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED", None, None)

    if len(cells) == 0:
        return (
            "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_AND_AMOUNTS_BLANK",
            None,
            [_blank_cell(), _blank_cell()],
        )
    if len(cells) == 1:
        _, kind, token = cells[0]
        if kind == "NUMERIC" and re.fullmatch(r"\d{1,4}", token.strip()):
            return (
                "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_ONLY_AMOUNTS_BLANK",
                token.strip(),
                [_blank_cell(), _blank_cell()],
            )
        return ("ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED", None, None)
    if len(cells) == 2:
        _, first_kind, first_token = cells[0]
        if (
            first_kind == "NUMERIC"
            and re.fullmatch(r"\d{1,4}", first_token.strip()) is not None
        ):
            return ("ROW_PRESENT_NOTE_PLUS_SINGLE_AMOUNT_UNRESOLVED", None, None)
        return ("ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_BLANK", None, cells)
    if len(cells) == 3:
        _, note_kind, note_token = cells[0]
        if (
            note_kind == "NUMERIC"
            and re.fullmatch(r"\d{1,4}", note_token.strip()) is not None
        ):
            return (
                "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE",
                note_token.strip(),
                cells[1:],
            )
        return ("ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED", None, None)
    return ("ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED", None, None)


def _layout_evidence_for_labels(
    lines: list[str],
    labels: Iterable[str],
) -> dict[str, Any] | None:
    label_options = tuple(str(label) for label in labels)
    boundaries = _statement_boundaries(lines)
    for offset, (start, token) in enumerate(boundaries):
        if token != "资产负债表":
            continue
        if not _is_exact_consolidated_balance_sheet_title(lines[start]):
            continue
        end = boundaries[offset + 1][0] if offset + 1 < len(boundaries) else len(lines)
        unit = _explicit_statement_unit(lines, start=start, end=end)
        if unit is None or str(unit) not in _AMOUNT_UNIT_SCALE:
            continue
        has_note_column = _statement_header_has_note_column(lines, start=start, end=end)
        amount_columns_declared = _amount_columns_declared(lines, start=start, end=end)
        column_anchors = _statement_column_anchors(lines, start=start, end=end)
        block = lines[start:end]
        normalized_block = [" ".join(line.split()) for line in block]

        for index in range(len(block)):
            if not _physical_line_starts_label(block[index], label_options):
                continue
            logical_row = _target_logical_row_window(block, index, label_options)
            normalized_logical_row = _target_logical_row_window(
                normalized_block, index, label_options
            )
            label_match = _wrapped_label_match(logical_row, label_options)
            if label_match is None:
                continue
            compact_row = re.sub(r"\s+", "", logical_row)
            matched_label = next(
                (
                    label
                    for label in label_options
                    if re.sub(r"\s+", "", label) in compact_row
                ),
                label_options[0],
            )
            suffix = logical_row[label_match.end() :]
            textual_note_reference = None
            cell_suffix = suffix
            if has_note_column:
                textual_note_reference, cell_suffix = _strip_explicit_textual_note_reference(suffix)
            cells = _ordered_numeric_or_dash_cells(cell_suffix)
            positional = None
            if amount_columns_declared and column_anchors is not None and logical_row == block[index]:
                physical_cells = _ordered_numeric_or_dash_cells(block[index])
                positional = _assign_cells_by_column_position(
                    cells=physical_cells,
                    anchors=column_anchors,
                    has_note_column=has_note_column,
                )
            if positional is not None:
                note_reference, amount_cells = positional
                if all(cell[1] == "BLANK" for cell in amount_cells):
                    layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN_BLANK_BOTH"
                else:
                    layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN"
            else:
                layout_state, note_reference, amount_cells = _classify_cells(
                    cells=cells,
                    has_note_column=has_note_column,
                    amount_columns_declared=amount_columns_declared,
                    textual_note_reference=textual_note_reference,
                )
                if amount_columns_declared and logical_row == block[index]:
                    recovered = _large_gap_two_amount_cells(block[index], label_options)
                    if (
                        recovered is not None
                        and not has_note_column
                        and layout_state == "ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED"
                    ):
                        layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN"
                        note_reference = None
                        amount_cells = recovered
                    elif (
                        recovered is not None
                        and has_note_column
                        and textual_note_reference is None
                        and layout_state == "ROW_PRESENT_NOTE_PLUS_SINGLE_AMOUNT_UNRESOLVED"
                    ):
                        layout_state = "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN_NOTE_BLANK"
                        note_reference = None
                        amount_cells = recovered

            current_kind = current_token = current_value_cny = None
            prior_kind = prior_token = None
            if amount_cells is not None and len(amount_cells) == 2:
                _, current_kind, current_token = amount_cells[0]
                _, prior_kind, prior_token = amount_cells[1]
                if current_kind == "NUMERIC":
                    current_value_cny = _scaled_numeric(current_token, str(unit))

            header_text = "\n".join(block[: min(len(block), 12)])
            return {
                "source_row_label": matched_label,
                "statement_unit": str(unit),
                "statement_has_note_column": bool(has_note_column),
                "statement_amount_columns_declared": bool(amount_columns_declared),
                "statement_header_sha256": hashlib.sha256(
                    header_text.encode("utf-8")
                ).hexdigest(),
                "note_reference": note_reference,
                "layout_proof_state": layout_state,
                "current_cell_kind": current_kind,
                "current_cell_token": current_token,
                "current_value_cny": current_value_cny,
                "prior_cell_kind": prior_kind,
                "prior_cell_token": prior_token,
                "logical_row_sha256": hashlib.sha256(
                    normalized_logical_row.encode("utf-8")
                ).hexdigest(),
                "raw_layout_row_sha256": hashlib.sha256(
                    logical_row.encode("utf-8")
                ).hexdigest(),
                "cell_count": len(cells),
                "zero_interpretation_applied": False,
                "private_classification_applied": False,
            }
    return None


def extract_accounting_balance_sheet_layout_proofs(
    text: str,
    *,
    fact_types: Iterable[str],
) -> dict[str, dict[str, Any]]:
    requested = sorted({str(value) for value in fact_types})
    unsupported = set(requested) - set(ACCOUNTING_BALANCE_SHEET_FACT_LABELS)
    _require(not unsupported, f"unsupported balance-sheet fact types: {sorted(unsupported)}")
    lines = _layout_text_lines(text)
    output: dict[str, dict[str, Any]] = {}
    for fact_type in requested:
        evidence = _layout_evidence_for_labels(
            lines,
            ACCOUNTING_BALANCE_SHEET_FACT_LABELS[fact_type],
        )
        if evidence is None:
            continue
        evidence["fact_type"] = fact_type
        evidence["parser_version"] = ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION
        output[fact_type] = evidence
    return output


def extract_accounting_balance_sheet_layout_proof(
    text: str,
    *,
    fact_type: str,
) -> dict[str, Any]:
    output = extract_accounting_balance_sheet_layout_proofs(
        text,
        fact_types=[fact_type],
    )
    evidence = output.get(str(fact_type))
    if evidence is None:
        raise ValueError(
            "official filing has no exact consolidated balance-sheet layout evidence "
            f"for {fact_type}"
        )
    return evidence


__all__ = [
    "ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION",
    "extract_accounting_balance_sheet_layout_proof",
    "extract_accounting_balance_sheet_layout_proofs",
]
