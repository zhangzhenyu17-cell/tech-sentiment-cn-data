from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from tech_sentiment.extended_filing_materialization import (
    _extended_document_identity,
    materialize_extended_filing_facts,
)
from tech_sentiment.official_filing_decision_drivers_v1 import (
    DECISION_DRIVER_PARSER_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/investment_decision_chain_operating_driver_primitives_v1.json"
SCOPE = ROOT / "data/reference/investment_decision_chain_v1_driver_scope_2026-09-30.csv"
WORKFLOW = ROOT / ".github/workflows/investment-decision-driver-primitives-v1.yml"


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_contract_freezes_exact_scope_and_research_firewalls() -> None:
    contract = _contract()
    assert contract["status"] == "FROZEN_OUTCOME_BLIND_PUBLIC_PIT_DRIVER_CAPTURE"
    assert contract["scope"]["symbol_count"] == 629
    assert hashlib.sha256(SCOPE.read_bytes()).hexdigest() == contract["scope"]["sha256"]
    frame = pd.read_csv(SCOPE, dtype={"symbol": str})
    assert frame["symbol"].nunique() == 629
    assert set(contract["parser"]["fact_types"]) == {
        "OPERATING_PROFIT_CN_GAAP",
        "TOTAL_PROFIT_CN_GAAP",
        "INCOME_TAX_EXPENSE_CN_GAAP",
    }
    assert all(value is False for value in contract["parser"]["semantic_boundaries"].values())
    assert contract["scope"]["new_domain_added"] is False
    assert contract["scope"]["new_benchmark_added"] is False
    assert all(value is False for value in contract["authority"].values())


def test_scope_is_exact_union_of_existing_frozen_inputs_without_expansion() -> None:
    contract = _contract()
    frame = pd.read_csv(SCOPE, dtype={"symbol": str})
    counts = frame["source_role"].value_counts().to_dict()
    assert counts == {
        "CROSS_SECTOR_A500_CURRENT_SCOPE": 358,
        "TECHNOLOGY_EXISTING_NUMERIC_PIT_BASELINE": 192,
        "CROSS_SECTOR_PRIORITY_CURRENT_SCOPE": 79,
    }
    assert contract["scope"]["construction"]["overlap_between_two_parts"] == 0
    assert len(frame) == 192 + 79 + 358


def test_existing_extended_materializer_default_is_preserved_and_parser_identity_is_injectable() -> None:
    signature = inspect.signature(materialize_extended_filing_facts)
    assert signature.parameters["parser_version"].default != DECISION_DRIVER_PARSER_VERSION
    assert callable(signature.parameters["fact_row_builder"].default)

    default_identity = _extended_document_identity(
        source_commit="a" * 40,
        symbol="600276",
        document_id="doc",
        attachment_url="https://static.cninfo.com.cn/doc.pdf",
    )
    decision_identity = _extended_document_identity(
        source_commit="a" * 40,
        symbol="600276",
        document_id="doc",
        attachment_url="https://static.cninfo.com.cn/doc.pdf",
        parser_version=DECISION_DRIVER_PARSER_VERSION,
    )
    assert default_identity.producer_version != decision_identity.producer_version
    assert decision_identity.producer_version == DECISION_DRIVER_PARSER_VERSION
def test_workflow_is_manual_only_and_uses_bounded_sharded_full_capture() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "workflow_run:" not in trigger_block
    assert 'FULL_SHARDS: "32"' in text
    assert "max-parallel: 4" in text
    assert "mode == 'pilot'" in text
    assert "mode == 'full'" in text
    assert "mode == 'repair'" in text
    assert "actions: read" in text
    assert "RECOVERY_SOURCE_RUN_ID: \"37097668812\"" in text
    assert "shard: [2, 3, 11, 14, 26, 31]" in text
    assert "gh run download \"$RECOVERY_SOURCE_RUN_ID\"" in text
    assert "--require-no-hard-failures" in text


def test_aggregate_builds_hash_manifest_without_private_semantics(tmp_path: Path) -> None:
    shard_root = tmp_path / "shards"
    for i, entity in enumerate(("600276.SH", "600893.SH")):
        root = shard_root / f"shard-{i}"
        root.mkdir(parents=True)
        facts = pd.DataFrame(
            [
                {
                    "entity_id": entity,
                    "period_end": "2026-06-30",
                    "fact_type": "OPERATING_PROFIT_CN_GAAP",
                    "value": 100.0 + i,
                    "unit": "CNY",
                    "evidence_available_date": "2026-08-20",
                    "publication_timestamp": "2026-08-19T10:00:00+08:00",
                    "source_identity": "CNINFO_OFFICIAL_DISCLOSURE",
                    "provider": "CNINFO",
                    "document_id": f"doc-{i}",
                    "revision_id": f"rev-{i}",
                    "document_url": f"https://static.cninfo.com.cn/{i}.pdf",
                    "document_sha256": str(i) * 64,
                    "parser_version": DECISION_DRIVER_PARSER_VERSION,
                }
            ]
        )
        facts.to_csv(root / "decision_driver_facts.csv", index=False)
        pd.DataFrame(
            [
                {
                    "source_identity": "CNINFO_OFFICIAL_DISCLOSURE",
                    "entity_id": entity,
                    "coverage_start": "2026-01-01",
                    "coverage_end": "2026-09-30",
                    "query_status": "COMPLETE_WINDOW",
                    "financial_documents": 1,
                    "parsed_documents": 1,
                    "soft_data_insufficient_documents": 0,
                }
            ]
        ).to_csv(root / "coverage.csv", index=False)
        pd.DataFrame(columns=["entity_id", "document_id", "error", "severity"]).to_csv(
            root / "errors.csv", index=False
        )
        (root / "stage_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "investment-decision-driver-shard-v1",
                    "parser_version": DECISION_DRIVER_PARSER_VERSION,
                    "source_commit": ("a" * 40 if i == 0 else "b" * 40),
                    "shard_index": i,
                    "shard_count": 2,
                    "historical_forward_outcome_read": False,
                    "prospective_forward_outcome_read": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    test_contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    test_contract["execution"]["full_shards"] = 2
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(test_contract) + "\n", encoding="utf-8")

    out = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/aggregate_investment_decision_driver_v1.py"),
            "--scope-csv",
            str(SCOPE),
            "--shard-root",
            str(shard_root),
            "--contract",
            str(contract_path),
            "--out-dir",
            str(out),
            "--require-no-hard-failures",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((out / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PUBLIC_OUTCOME_BLIND_RAW_DRIVER_BUNDLE"
    assert manifest["scope_symbol_count"] == 629
    assert manifest["hard_failure_rows"] == 0
    assert manifest["shard_indices"] == [0, 1]
    assert manifest["source_commits"] == ["a" * 40, "b" * 40]
    assert manifest["shard_source_commits"] == {"0": "a" * 40, "1": "b" * 40}
    assert manifest["private_model_semantics_included"] is False
    assert manifest["historical_forward_outcome_read"] is False
    assert manifest["prospective_forward_outcome_read"] is False
    assert manifest["evidence_qualification_changed"] is False
    assert manifest["production_changed"] is False
    assert manifest["trading_authority_changed"] is False
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((out / filename).read_bytes()).hexdigest() == digest


def test_recovery_contract_is_exact_and_does_not_expand_authority() -> None:
    contract = _contract()
    recovery = contract["recovery"]
    assert recovery["source_run_id"] == 37097668812
    assert recovery["source_head_sha"] == "b4fbbdc4096fdb18f331f0ddb809c8dafbf544c2"
    assert recovery["repair_shards"] == [2, 3, 11, 14, 26, 31]
    assert recovery["policy"] == "REPAIR_ONLY_AFFECTED_SHARDS_REUSE_OTHER_PINNED_SUCCESS_ARTIFACTS"
    assert recovery["outcome_read"] is False
    assert recovery["evidence_qualification_change"] is False
    assert recovery["production_change"] is False
    assert recovery["trading_authority_change"] is False
