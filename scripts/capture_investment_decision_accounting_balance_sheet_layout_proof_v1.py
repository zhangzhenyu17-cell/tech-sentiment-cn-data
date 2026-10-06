from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from tech_sentiment.official_filing_accounting_balance_sheet_layout_proof_v1 import (
    ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
    extract_accounting_balance_sheet_layout_proof,
    extract_accounting_balance_sheet_layout_proofs,
)
from tech_sentiment.official_filing_accounting_balance_sheet_v1 import (
    ACCOUNTING_BALANCE_SHEET_FACT_LABELS,
)
from tech_sentiment.official_filing_facts import (
    download_official_document,
    extract_pdf_text,
)


SCHEMA_VERSION = "investment-decision-accounting-balance-sheet-layout-proof-capture-v1"
REQUIRED_REQUEST_COLUMNS = {
    "entity_id",
    "period_end",
    "fact_type",
    "source_row_label",
    "statement_unit",
    "source_identity",
    "provider",
    "document_id",
    "document_url",
    "document_sha256",
    "source_row_sha256",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_request(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = REQUIRED_REQUEST_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"layout-proof request columns missing: {sorted(missing)}")
    if frame.empty:
        raise ValueError("layout-proof request is empty")
    if frame.duplicated(["entity_id", "period_end", "fact_type", "document_id"]).any():
        raise ValueError("layout-proof request contains duplicate keys")
    unsupported = set(frame["fact_type"]) - set(ACCOUNTING_BALANCE_SHEET_FACT_LABELS)
    if unsupported:
        raise ValueError(
            f"layout-proof request has unsupported fact types: {sorted(unsupported)}"
        )
    return frame


def _document_identity(group: pd.DataFrame) -> tuple[str, str, str]:
    document_ids = set(group["document_id"].astype(str))
    urls = set(group["document_url"].astype(str))
    hashes = set(group["document_sha256"].astype(str))
    if len(document_ids) != 1 or len(urls) != 1 or len(hashes) != 1:
        raise ValueError("layout-proof document identity is not unique")
    document_id = next(iter(document_ids))
    url = next(iter(urls))
    digest = next(iter(hashes))
    if len(digest) != 64:
        raise ValueError(f"layout-proof document sha malformed: {document_id}")
    return document_id, url, digest


def _extract_balance_sheet_layout_text(content: bytes) -> str:
    """Extract only official consolidated balance-sheet page windows when possible.

    PyMuPDF is used only as a fast page locator over the same immutable PDF
    bytes. Evidence text itself is still extracted with pypdf layout mode, the
    same engine used by the frozen row-evidence parser. If the locator cannot
    establish candidate pages, the conservative full-document extractor is
    used unchanged.
    """
    try:
        import fitz
        from pypdf import PdfReader
        import io
    except ImportError:
        return extract_pdf_text(content)

    candidates: list[int] = []
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        try:
            for index, page in enumerate(doc):
                compact = "".join((page.get_text("text") or "").split())
                if "合并资产负债表" in compact:
                    candidates.append(index)
        finally:
            doc.close()
    except Exception:
        return extract_pdf_text(content)
    if not candidates:
        return extract_pdf_text(content)

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        page_ids: set[int] = set()
        for index in candidates:
            for page_id in range(max(0, index - 1), min(len(reader.pages), index + 9)):
                page_ids.add(page_id)
        parts: list[str] = []
        for page_id in sorted(page_ids):
            text = reader.pages[page_id].extract_text(extraction_mode="layout") or ""
            if text.strip():
                parts.append(text)
        targeted = "\n".join(parts).strip()
        if targeted and "合并资产负债表" in "".join(targeted.split()):
            return targeted
    except Exception:
        pass
    return extract_pdf_text(content)


def _document_text(
    *,
    document_id: str,
    url: str,
    expected_sha256: str,
    cache_dir: Path,
) -> str:
    pdf_dir = cache_dir / "documents"
    text_dir = cache_dir / "text"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{expected_sha256}.pdf"
    text_path = text_dir / f"{expected_sha256}.txt"

    if text_path.exists() and pdf_path.exists():
        content = pdf_path.read_bytes()
        if _sha256(content) != expected_sha256:
            raise ValueError(f"cached document sha mismatch: {document_id}")
        return text_path.read_text(encoding="utf-8")

    if pdf_path.exists():
        content = pdf_path.read_bytes()
        if _sha256(content) != expected_sha256:
            raise ValueError(f"cached document sha mismatch: {document_id}")
    else:
        downloaded = download_official_document(url)
        if downloaded.sha256 != expected_sha256:
            raise ValueError(
                f"official document sha mismatch: {document_id}: "
                f"{downloaded.sha256} != {expected_sha256}"
            )
        content = downloaded.content
        pdf_path.write_bytes(content)

    text = _extract_balance_sheet_layout_text(content)
    text_path.write_text(text, encoding="utf-8")
    return text


def _capture_group(
    group: pd.DataFrame,
    *,
    cache_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    document_id, url, expected_sha = _document_identity(group)
    try:
        text = _document_text(
            document_id=document_id,
            url=url,
            expected_sha256=expected_sha,
            cache_dir=cache_dir,
        )
    except Exception as exc:
        return [], [{
            "document_id": document_id,
            "entity_id": str(group.iloc[0]["entity_id"]),
            "fact_type": "DOCUMENT",
            "error": f"{type(exc).__name__}: {exc}",
        }]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    requested_fact_types = sorted(set(group["fact_type"].astype(str)))
    proofs = extract_accounting_balance_sheet_layout_proofs(
        text,
        fact_types=requested_fact_types,
    )
    for request in group.to_dict("records"):
        fact_type = str(request["fact_type"])
        try:
            proof = proofs.get(fact_type)
            if proof is None:
                raise ValueError(
                    "official filing has no exact consolidated balance-sheet layout evidence "
                    f"for {fact_type}"
                )
            row_hash_match = (
                str(proof["logical_row_sha256"])
                == str(request["source_row_sha256"])
            )
            unit_match = (
                str(proof["statement_unit"])
                == str(request["statement_unit"])
            )
            if not row_hash_match:
                raise ValueError(
                    "source row sha mismatch against prior public row evidence"
                )
            if not unit_match:
                raise ValueError(
                    "statement unit mismatch against prior public row evidence"
                )
            rows.append({
                "schema_version": SCHEMA_VERSION,
                "entity_id": str(request["entity_id"]),
                "period_end": str(request["period_end"]),
                "fact_type": fact_type,
                "source_row_label": str(proof["source_row_label"]),
                "statement_unit": str(proof["statement_unit"]),
                "source_identity": str(request["source_identity"]),
                "provider": str(request["provider"]),
                "document_id": document_id,
                "document_url": url,
                "document_sha256": expected_sha,
                "source_row_sha256": str(request["source_row_sha256"]),
                "parser_version": ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
                "statement_has_note_column": bool(
                    proof["statement_has_note_column"]
                ),
                "statement_amount_columns_declared": bool(
                    proof["statement_amount_columns_declared"]
                ),
                "statement_header_sha256": str(
                    proof["statement_header_sha256"]
                ),
                "note_reference": proof["note_reference"],
                "layout_proof_state": str(proof["layout_proof_state"]),
                "current_cell_kind": proof["current_cell_kind"],
                "current_cell_token": proof["current_cell_token"],
                "current_value_cny": proof["current_value_cny"],
                "prior_cell_kind": proof["prior_cell_kind"],
                "prior_cell_token": proof["prior_cell_token"],
                "logical_row_sha256": str(proof["logical_row_sha256"]),
                "cell_count": int(proof["cell_count"]),
                "source_row_sha256_matches_request": True,
                "statement_unit_matches_request": True,
                "zero_interpretation_applied": False,
                "private_classification_applied": False,
            })
        except Exception as exc:
            errors.append({
                "document_id": document_id,
                "entity_id": str(request["entity_id"]),
                "fact_type": fact_type,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-csv", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--require-no-errors", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", str(args.source_commit)):
        raise ValueError("source-commit must be a 40-character lowercase git sha")
    if args.max_documents is not None and args.max_documents < 1:
        raise ValueError("max-documents must be positive")
    if args.max_workers < 1 or args.max_workers > 4:
        raise ValueError("max-workers must be between 1 and 4")

    request = _load_request(args.request_csv)
    groups = list(request.groupby("document_id", sort=True))
    if args.max_documents is not None:
        groups = groups[: args.max_documents]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    completed = 0
    with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(_capture_group, group.copy(), cache_dir=args.cache_dir): str(document_id)
            for document_id, group in groups
        }
        for future in as_completed(futures):
            document_id = futures[future]
            try:
                part_rows, part_errors = future.result()
            except Exception as exc:
                part_rows = []
                part_errors = [{
                    "document_id": document_id,
                    "entity_id": "",
                    "fact_type": "DOCUMENT",
                    "error": f"{type(exc).__name__}: {exc}",
                }]
            rows.extend(part_rows)
            errors.extend(part_errors)
            completed += 1
            if completed % 10 == 0 or completed == len(groups):
                print(json.dumps({
                    "event": "LAYOUT_PROOF_CAPTURE_PROGRESS",
                    "completed_documents": completed,
                    "total_documents": len(groups),
                    "captured_rows": len(rows),
                    "error_rows": len(errors),
                }, ensure_ascii=False), flush=True)

    output_columns = [
        "schema_version",
        "source_commit",
        "entity_id",
        "period_end",
        "fact_type",
        "source_row_label",
        "statement_unit",
        "source_identity",
        "provider",
        "document_id",
        "document_url",
        "document_sha256",
        "source_row_sha256",
        "parser_version",
        "statement_has_note_column",
        "statement_amount_columns_declared",
        "statement_header_sha256",
        "note_reference",
        "layout_proof_state",
        "current_cell_kind",
        "current_cell_token",
        "current_value_cny",
        "prior_cell_kind",
        "prior_cell_token",
        "logical_row_sha256",
        "cell_count",
        "source_row_sha256_matches_request",
        "statement_unit_matches_request",
        "zero_interpretation_applied",
        "private_classification_applied",
    ]
    output = pd.DataFrame(rows)
    if len(output):
        output.insert(1, "source_commit", str(args.source_commit))
        output = output.reindex(columns=output_columns)
    else:
        output = pd.DataFrame(columns=output_columns)
    if len(output):
        output = output.sort_values(
            ["entity_id", "period_end", "fact_type", "document_id"]
        ).reset_index(drop=True)
    error_frame = pd.DataFrame(
        errors,
        columns=["document_id", "entity_id", "fact_type", "error"],
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        args.out_dir / "accounting_balance_sheet_layout_proof_evidence.csv"
    )
    error_path = args.out_dir / "errors.csv"
    output.to_csv(output_path, index=False)
    error_frame.to_csv(error_path, index=False)

    counts = (
        output["layout_proof_state"].value_counts().sort_index().to_dict()
        if len(output)
        else {}
    )
    manifest = {
        "schema_version": (
            "investment-decision-accounting-balance-sheet-"
            "layout-proof-capture-manifest-v1"
        ),
        "status": (
            "PUBLIC_OFFICIAL_LAYOUT_EVIDENCE_CAPTURED_"
            "NO_PRIVATE_QUALIFICATION"
        ),
        "parser_version": ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
        "source_commit": str(args.source_commit),
        "request_sha256": _sha256(args.request_csv.read_bytes()),
        "request_semantics_interpreted": False,
        "requested_rows_in_selected_documents": int(
            sum(len(group) for _, group in groups)
        ),
        "requested_document_count": int(len(groups)),
        "captured_row_count": int(len(output)),
        "error_row_count": int(len(error_frame)),
        "layout_proof_state_counts": {
            str(key): int(value) for key, value in counts.items()
        },
        "source_row_hash_match_required": True,
        "statement_unit_match_required": True,
        "zero_interpretation_applied": False,
        "private_classification_applied": False,
        "historical_forward_outcome_read": False,
        "prospective_forward_outcome_read": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "portfolio_action_emitted": False,
        "trading_authority_changed": False,
        "files": {
            output_path.name: _sha256(output_path.read_bytes()),
            error_path.name: _sha256(error_path.read_bytes()),
        },
    }
    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False), flush=True)

    if args.require_no_errors and len(error_frame):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
