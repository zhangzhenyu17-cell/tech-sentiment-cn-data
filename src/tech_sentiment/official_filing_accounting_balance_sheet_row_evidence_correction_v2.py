from __future__ import annotations

import re
from typing import Any

from .official_filing_accounting_balance_sheet_layout_proof_v1 import (
    ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
    extract_accounting_balance_sheet_layout_proof,
)

CORRECTION_PARSER_VERSION = (
    "official-filing-accounting-balance-sheet-row-evidence-correction-v2-"
    "exact-statement-title"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_LAYOUT_STATES = {
    "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN",
    "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_BLANK",
    "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE",
    "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_ONLY_AMOUNTS_BLANK",
    "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_AND_AMOUNTS_BLANK",
    "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN",
    "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN_BLANK_BOTH",
    "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN",
    "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN_NOTE_BLANK",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def extract_row_evidence_correction(
    text: str,
    *,
    fact_type: str,
    supersedes_source_row_sha256: str,
) -> dict[str, Any]:
    _require(_SHA256.fullmatch(str(supersedes_source_row_sha256)) is not None, "superseded source-row sha malformed")
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type=fact_type)
    _require(proof["layout_proof_state"] in _SAFE_LAYOUT_STATES, "corrected row layout ownership not proven")
    corrected_sha = str(proof["logical_row_sha256"])
    _require(_SHA256.fullmatch(corrected_sha) is not None, "corrected source-row sha malformed")
    _require(corrected_sha != supersedes_source_row_sha256, "correction does not change source-row identity")
    return {
        "source_row_label": proof["source_row_label"],
        "statement_unit": proof["statement_unit"],
        "statement_has_note_column": proof["statement_has_note_column"],
        "note_reference": proof["note_reference"],
        "row_layout_state": "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_CORRECTED_EXACT_STATEMENT",
        "current_cell_kind": proof["current_cell_kind"],
        "current_cell_token": proof["current_cell_token"],
        "current_value_cny": proof["current_value_cny"],
        "prior_cell_kind": proof["prior_cell_kind"],
        "prior_cell_token": proof["prior_cell_token"],
        "source_row_sha256": corrected_sha,
        "supersedes_source_row_sha256": supersedes_source_row_sha256,
        "source_row_mismatch_confirmed": True,
        "layout_proof_parser_version": ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
        "parser_version": CORRECTION_PARSER_VERSION,
        "zero_interpretation_applied": False,
        "private_classification_applied": False,
    }


__all__ = ["CORRECTION_PARSER_VERSION", "extract_row_evidence_correction"]
