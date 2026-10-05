from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from tech_sentiment.official_filing_accounting_balance_sheet_v1 import (
    ACCOUNTING_BALANCE_SHEET_FACT_LABELS,
    ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/investment_decision_chain_accounting_balance_sheet_row_evidence_v1.json"
SCOPE = ROOT / "data/reference/investment_decision_chain_v1_driver_scope_2026-09-30.csv"
WORKFLOW = ROOT / ".github/workflows/investment-decision-accounting-balance-sheet-row-evidence-v1.yml"


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_contract_freezes_same_629_scope_as_raw_row_evidence_only() -> None:
    contract = _contract()
    assert contract["status"] == "FROZEN_OUTCOME_BLIND_PUBLIC_PIT_BALANCE_SHEET_ROW_EVIDENCE_CAPTURE"
    assert contract["scope"]["symbol_count"] == 629
    assert hashlib.sha256(SCOPE.read_bytes()).hexdigest() == contract["scope"]["sha256"]
    assert contract["parser"]["parser_version"] == ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION
    assert contract["parser"]["fact_types"] == list(ACCOUNTING_BALANCE_SHEET_FACT_LABELS)
    assert contract["parser"]["fact_type_count"] == 51
    assert contract["parser"]["raw_row_evidence_only"] is True
    assert contract["parser"]["numeric_fact_recovery_role"] is False
    assert contract["parser"]["dash_or_blank_interpreted_as_zero"] is False
    assert contract["parser"]["private_classification_included"] is False
    assert contract["parser"]["missing_values_zero_imputed"] is False
    assert contract["cache_reuse"]["query_index_cache_reuse_allowed"] is True
    assert contract["cache_reuse"]["parsed_document_fact_cache_reuse_allowed"] is False
    assert all(value is False for value in contract["authority"].values())


def test_workflow_is_manual_only_bounded_and_reuses_only_query_index_cache() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "workflow_run:" not in trigger_block
    assert 'FULL_SHARDS: "32"' in text
    assert "max-parallel: 4" in text
    assert "--legacy-query-checkpoint-source-commit" in text
    assert "7afa41d06eeb6443f11fa1482ec58364224bbbdd" in text
    assert "--require-no-hard-failures" in text


def test_aggregate_preserves_dash_as_raw_evidence_without_zero_or_private_semantics(tmp_path: Path) -> None:
    shard_root = tmp_path / "shards"
    rows = [
        ("600276.SH", "NUMERIC", "100", "1000000.0"),
        ("600893.SH", "DASH", "-", ""),
    ]
    for i, (entity, kind, token, current_value) in enumerate(rows):
        root = shard_root / f"shard-{i}"
        root.mkdir(parents=True)
        pd.DataFrame([
            {
                "entity_id": entity,
                "period_end": "2026-06-30",
                "fact_type": "MONETARY_FUNDS",
                "value": current_value,
                "unit": "CNY",
                "evidence_available_date": "2026-08-20",
                "publication_timestamp": "2026-08-19T10:00:00+08:00",
                "source_identity": "CNINFO_OFFICIAL_DISCLOSURE",
                "provider": "CNINFO",
                "document_id": f"doc-{i}",
                "revision_id": f"rev-{i}",
                "document_url": f"https://static.cninfo.com.cn/{i}.pdf",
                "document_sha256": str(i) * 64,
                "parser_version": ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION,
                "source_row_label": "货币资金",
                "statement_unit": "万元",
                "statement_has_note_column": False,
                "note_reference": "",
                "row_layout_state": "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN",
                "current_cell_kind": kind,
                "current_cell_token": token,
                "current_value_cny": current_value,
                "prior_cell_kind": "NUMERIC",
                "prior_cell_token": "90",
                "source_row_sha256": "a" * 64,
                "zero_interpretation_applied": False,
                "private_classification_applied": False,
            }
        ]).to_csv(root / "accounting_balance_sheet_row_evidence.csv", index=False)
        pd.DataFrame([{
            "source_identity": "CNINFO_OFFICIAL_DISCLOSURE",
            "entity_id": entity,
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-09-30",
            "query_status": "COMPLETE_WINDOW",
            "financial_documents": 1,
            "parsed_documents": 1,
            "soft_data_insufficient_documents": 0,
        }]).to_csv(root / "coverage.csv", index=False)
        pd.DataFrame(columns=["entity_id", "document_id", "error", "severity"]).to_csv(root / "errors.csv", index=False)
        (root / "stage_manifest.json").write_text(json.dumps({
            "schema_version": "investment-decision-accounting-balance-sheet-row-evidence-shard-v1",
            "parser_version": ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION,
            "source_commit": ("a" * 40 if i == 0 else "b" * 40),
            "shard_index": i,
            "shard_count": 2,
            "dash_or_blank_interpreted_as_zero": False,
            "private_operating_financing_classification_included": False,
            "historical_forward_outcome_read": False,
            "prospective_forward_outcome_read": False,
        }) + "\n", encoding="utf-8")

    contract = _contract()
    contract["execution"]["full_shards"] = 2
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    completed = subprocess.run([
        sys.executable,
        str(ROOT / "scripts/aggregate_investment_decision_accounting_balance_sheet_row_evidence_v1.py"),
        "--scope-csv", str(SCOPE),
        "--shard-root", str(shard_root),
        "--contract", str(contract_path),
        "--out-dir", str(out),
        "--require-no-hard-failures",
    ], cwd=ROOT, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((out / "bundle_manifest.json").read_text(encoding="utf-8"))
    evidence = pd.read_csv(out / "accounting_balance_sheet_row_evidence.csv", dtype=str, keep_default_na=False)
    assert manifest["status"] == "PUBLIC_OUTCOME_BLIND_RAW_BALANCE_SHEET_ROW_EVIDENCE_BUNDLE"
    assert manifest["scope_symbol_count"] == 629
    assert manifest["hard_failure_rows"] == 0
    assert manifest["dash_or_blank_interpreted_as_zero"] is False
    assert manifest["private_model_semantics_included"] is False
    assert manifest["evidence_qualification_changed"] is False
    dash = evidence.loc[evidence["current_cell_kind"].eq("DASH")].iloc[0]
    assert dash["current_cell_token"] == "-"
    assert dash["current_value_cny"] == ""
    assert dash["zero_interpretation_applied"] == "False"
    assert dash["private_classification_applied"] == "False"
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((out / filename).read_bytes()).hexdigest() == digest
