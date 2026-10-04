from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from tech_sentiment.official_filing_nonrecurring_disclosure_v1 import (
    NONRECURRING_DISCLOSURE_FACT_LABELS,
    NONRECURRING_DISCLOSURE_PARSER_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/investment_decision_chain_nonrecurring_disclosure_v1.json"
SCOPE = ROOT / "data/reference/investment_decision_chain_v1_driver_scope_2026-09-30.csv"
WORKFLOW = ROOT / ".github/workflows/investment-decision-nonrecurring-disclosure-v1.yml"


def test_contract_is_frozen_exact_scope_raw_only_and_manual_only() -> None:
    contract = json.loads(CONTRACT.read_text())
    assert contract["contract_id"] == "INVESTMENT_DECISION_CHAIN_NONRECURRING_DISCLOSURE_V1"
    assert contract["decision_date"] == "2026-09-30"
    assert contract["scope"]["symbol_count"] == 629
    assert contract["scope"]["sha256"] == hashlib.sha256(SCOPE.read_bytes()).hexdigest()
    assert contract["parser"]["parser_version"] == NONRECURRING_DISCLOSURE_PARSER_VERSION
    assert contract["parser"]["fact_types"] == list(NONRECURRING_DISCLOSURE_FACT_LABELS)
    assert contract["parser"]["fact_type_count"] == 25
    assert contract["parser"]["raw_explicit_disclosure_rows_only"] is True
    assert contract["parser"]["private_core_unusual_classification_included"] is False
    assert contract["parser"]["synthetic_unusual_aggregate_included"] is False
    assert contract["parser"]["missing_values_zero_imputed"] is False
    assert contract["execution"]["workflow_dispatch_only"] is True
    assert contract["execution"]["automatic_triggers_allowed"] is False
    assert contract["execution"]["full_shards"] == 32
    assert all(value is False for value in contract["authority"].values())


def test_workflow_is_dispatch_only_and_has_pilot_before_full() -> None:
    text = WORKFLOW.read_text()
    trigger = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "push:" not in trigger
    assert "pull_request:" not in trigger
    assert "workflow_run:" not in trigger
    assert "pilot:" in text
    assert "full:" in text
    assert text.index("pilot:") < text.index("full:")
    assert "--max-financial-documents-per-symbol 4" in text
    assert "--require-no-hard-failures" in text


def test_aggregate_preserves_raw_roles_and_fail_closed_flags(tmp_path: Path) -> None:
    scope = tmp_path / "scope.csv"
    pd.DataFrame({"symbol": ["000001", "600519"]}).to_csv(scope, index=False)
    contract = json.loads(CONTRACT.read_text())
    contract["scope"] = {
        **contract["scope"],
        "path": str(scope),
        "symbol_count": 2,
        "sha256": hashlib.sha256(scope.read_bytes()).hexdigest(),
    }
    contract["execution"]["full_shards"] = 2
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract))
    shard_root = tmp_path / "shards"
    columns = [
        "entity_id", "period_end", "fact_type", "value", "unit",
        "evidence_available_date", "publication_timestamp", "source_identity",
        "provider", "document_id", "revision_id", "document_url",
        "document_sha256", "parser_version", "disclosure_role",
        "source_row_label", "filing_title",
    ]
    rows = [
        ["000001.SZ", "2026-03-31", "NR_REPORTED_NET_TOTAL", 75.0, "CNY", "2026-04-30", "2026-04-29T18:00:00+08:00", "CNINFO_OFFICIAL_DISCLOSURE", "CNINFO", "d1", "r1", "https://static.cninfo.com.cn/1.pdf", "a" * 64, NONRECURRING_DISCLOSURE_PARSER_VERSION, "REPORTED_NET_TOTAL", "合计", "2026年第一季度报告"],
        ["600519.SH", "2026-06-30", "NR_INCOME_TAX_EFFECT", 25.0, "CNY", "2026-08-13", "2026-08-12T18:00:00+08:00", "CNINFO_OFFICIAL_DISCLOSURE", "CNINFO", "d2", "r2", "https://static.cninfo.com.cn/2.pdf", "b" * 64, NONRECURRING_DISCLOSURE_PARSER_VERSION, "INCOME_TAX_EFFECT", "减:所得税影响额", "2026年半年度报告"],
    ]
    source_commit = "1" * 40
    for idx, row in enumerate(rows):
        out = shard_root / f"shard-{idx}"
        out.mkdir(parents=True)
        pd.DataFrame([row], columns=columns).to_csv(out / "nonrecurring_disclosure_facts.csv", index=False)
        pd.DataFrame([{"entity_id": row[0], "coverage_start": "2026-01-01", "coverage_end": "2026-09-30", "query_status": "COMPLETE_WINDOW"}]).to_csv(out / "coverage.csv", index=False)
        pd.DataFrame(columns=["entity_id", "document_id", "error", "severity"]).to_csv(out / "errors.csv", index=False)
        (out / "stage_manifest.json").write_text(json.dumps({"shard_index": idx, "source_commit": source_commit}))
    final = tmp_path / "final"
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/aggregate_investment_decision_nonrecurring_disclosure_v1.py"),
        "--scope-csv", str(scope), "--shard-root", str(shard_root),
        "--contract", str(contract_path), "--out-dir", str(final),
        "--require-no-hard-failures",
    ], check=True, cwd=ROOT)
    manifest = json.loads((final / "bundle_manifest.json").read_text())
    assert manifest["hard_failure_rows"] == 0
    assert manifest["scope_symbol_count"] == 2
    assert manifest["shard_manifest_count"] == 2
    assert manifest["private_model_semantics_included"] is False
    assert manifest["synthetic_unusual_aggregate_included"] is False
    assert manifest["missing_values_zero_imputed"] is False
    assert manifest["historical_forward_outcome_read"] is False
    assert manifest["evidence_qualification_changed"] is False
    facts = pd.read_csv(final / "nonrecurring_disclosure_facts.csv")
    assert set(facts["disclosure_role"]) == {"REPORTED_NET_TOTAL", "INCOME_TAX_EFFECT"}
