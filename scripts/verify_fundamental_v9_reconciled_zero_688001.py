from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.official_filing_extended_pit import (
    EXTENDED_FILING_PARSER_VERSION,
    _subtotal_reconciled_zero_non_current_debt,
    build_extended_filing_fact_rows,
)
from tech_sentiment.official_filing_facts import (
    _normalize_text_lines,
    download_official_document,
    extract_pdf_text,
)

TARGET_ENTITY = "688001.SH"
TARGET_PERIOD = "2025-03-31"
TARGET_TITLE = "苏州华兴源创科技股份有限公司2025年第一季度报告"
TARGET_AVAILABLE_DATE = "2025-04-30"
TARGET_PUBLICATION_TIMESTAMP = "2025-04-30T00:00:00"
TARGET_SOURCE_IDENTITY = "CNINFO_ANNOUNCEMENT_ARCHIVE"
TARGET_PROVIDER = "CNINFO"
TARGET_DOCUMENT_ID = "1223423562"
TARGET_DOCUMENT_URL = "https://static.cninfo.com.cn/finalpage/2025-04-30/1223423562.PDF"
TARGET_DOCUMENT_SHA256 = "5389cb110666f96a8d75968c2b11d07a851e1c671eaa623e7da4128dc1a3fa43"
TARGET_REVISION_ID = f"DOCUMENT:{TARGET_DOCUMENT_ID}:SHA256:{TARGET_DOCUMENT_SHA256}"
TARGET_PARSER_VERSION = (
    "official-filing-extended-pit-primitives-v9-subtotal-reconciled-zero-safe"
)

EXPECTED_FACTS = {
    "MONETARY_FUNDS": 501_632_696.43,
    "SHORT_TERM_BORROWINGS": 444_791_839.55,
    "CURRENT_PORTION_NON_CURRENT_LIABILITIES": 17_117_288.53,
    "LONG_TERM_BORROWINGS": 0.0,
    "BONDS_PAYABLE": 715_144_637.75,
    "LEASE_LIABILITIES": 27_048_831.94,
}


def _assert_close(actual: float, expected: float, *, fact_type: str) -> None:
    if abs(float(actual) - float(expected)) > 0.005:
        raise ValueError(
            f"{fact_type} value drift: expected={expected} actual={actual}"
        )


def verify_target(output_dir: Path) -> dict[str, object]:
    if EXTENDED_FILING_PARSER_VERSION != TARGET_PARSER_VERSION:
        raise ValueError(
            "V9 parser identity drift: "
            f"expected={TARGET_PARSER_VERSION} actual={EXTENDED_FILING_PARSER_VERSION}"
        )

    downloaded = download_official_document(TARGET_DOCUMENT_URL)
    if downloaded.sha256 != TARGET_DOCUMENT_SHA256:
        raise ValueError(
            "official document SHA256 drift: "
            f"expected={TARGET_DOCUMENT_SHA256} actual={downloaded.sha256}"
        )

    text = extract_pdf_text(downloaded.content)
    reconciled = _subtotal_reconciled_zero_non_current_debt(
        _normalize_text_lines(text)
    )
    if reconciled != {"LONG_TERM_BORROWINGS": 0.0}:
        raise ValueError(
            "target reconciliation result drift: "
            f"expected={{'LONG_TERM_BORROWINGS': 0.0}} actual={reconciled}"
        )

    facts = build_extended_filing_fact_rows(
        entity_id=TARGET_ENTITY,
        title=TARGET_TITLE,
        evidence_available_date=TARGET_AVAILABLE_DATE,
        publication_timestamp=TARGET_PUBLICATION_TIMESTAMP,
        source_identity=TARGET_SOURCE_IDENTITY,
        provider=TARGET_PROVIDER,
        document_id=TARGET_DOCUMENT_ID,
        revision_id=TARGET_REVISION_ID,
        document_url=TARGET_DOCUMENT_URL,
        document_sha256=downloaded.sha256,
        text=text,
    )
    selected = facts.set_index("fact_type")
    for fact_type, expected in EXPECTED_FACTS.items():
        if fact_type not in selected.index:
            raise ValueError(f"required target fact missing: {fact_type}")
        row = selected.loc[fact_type]
        if str(row["unit"]) != "CNY":
            raise ValueError(f"{fact_type} unit drift: {row['unit']}")
        if str(row["parser_version"]) != TARGET_PARSER_VERSION:
            raise ValueError(f"{fact_type} parser identity drift")
        _assert_close(float(row["value"]), expected, fact_type=fact_type)

    output_dir.mkdir(parents=True, exist_ok=True)
    facts.to_csv(output_dir / "target_facts.csv", index=False, lineterminator="\n")
    receipt = {
        "version": "fundamental_v9_reconciled_zero_target_verification_v1",
        "date": "2026-09-26",
        "status": "OUTCOME_BLIND_TARGET_VERIFIED_RECONCILED_ZERO_FACT_MATERIALIZED",
        "target": {
            "entity_id": TARGET_ENTITY,
            "report_period": TARGET_PERIOD,
            "evidence_available_date": TARGET_AVAILABLE_DATE,
            "document_id": TARGET_DOCUMENT_ID,
            "document_url": TARGET_DOCUMENT_URL,
            "document_sha256": TARGET_DOCUMENT_SHA256,
            "revision_id": TARGET_REVISION_ID,
        },
        "parser": {
            "version": TARGET_PARSER_VERSION,
            "reconciliation_route": "SUBTOTAL_RECONCILED_ZERO",
            "reconciled_zero_facts": reconciled,
            "blank_or_dash_directly_mapped_to_zero": False,
        },
        "verified_facts": {
            fact_type: float(selected.loc[fact_type, "value"])
            for fact_type in EXPECTED_FACTS
        },
        "provenance": {
            "source_identity": TARGET_SOURCE_IDENTITY,
            "provider": TARGET_PROVIDER,
            "required_document_sha256_matched": True,
            "publication_timestamp": TARGET_PUBLICATION_TIMESTAMP,
            "evidence_available_date": TARGET_AVAILABLE_DATE,
        },
        "governance": {
            "outcome_read": False,
            "historical_outcome_reread": False,
            "validation_executed": False,
            "private_evidence_qualification_changed_by_public_workflow": False,
            "public_artifact_automatically_grants_private_qualification": False,
            "model_changed": False,
            "threshold_changed": False,
            "universe_changed": False,
            "production_changed": False,
            "trading_authority_changed": False,
        },
    }
    (output_dir / "verification_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen 688001.SH 2025Q1 V9 subtotal-reconciled-zero "
            "target from the exact official CNINFO document. Outcome-blind only."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = verify_target(args.output_dir)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
