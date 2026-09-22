from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_fundamental_extended_pit_coverage.py"


def _module():
    spec = importlib.util.spec_from_file_location("audit_extended_pit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scope() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "688001", "market": "SH", "historical_scope": "STAR50"},
            {"symbol": "300001", "market": "SZ", "historical_scope": "CHINEXT50"},
        ]
    )


def _facts() -> pd.DataFrame:
    base = {
        "period_end": "2025-06-30",
        "unit": "CNY",
        "evidence_available_date": "2025-08-30",
        "publication_timestamp": "2025-08-29T10:00:00+08:00",
        "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        "provider": "CNINFO",
        "document_url": "https://static.cninfo.com.cn/sample.pdf",
        "document_sha256": "a" * 64,
        "parser_version": "official-filing-extended-pit-primitives-v1",
    }
    rows = []
    for entity, value in (("688001.SH", 10.0), ("300001.SZ", 20.0)):
        rows.append(
            {
                **base,
                "entity_id": entity,
                "fact_type": "MONETARY_FUNDS",
                "value": value,
                "document_id": f"{entity}-a",
                "revision_id": f"{entity}-rev-a",
            }
        )
    rows.extend(
        [
            {
                **base,
                "entity_id": "688001.SH",
                "fact_type": "CAPEX_CASH_PAID",
                "value": 3.0,
                "document_id": "688001-capex-a",
                "revision_id": "688001-capex-rev-a",
            },
            {
                **base,
                "entity_id": "688001.SH",
                "fact_type": "CAPEX_CASH_PAID",
                "value": 4.0,
                "document_id": "688001-capex-b",
                "revision_id": "688001-capex-rev-b",
            },
        ]
    )
    return pd.DataFrame(rows)


def _coverage() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "entity_id": "688001.SH",
                "coverage_start": "2022-01-04",
                "coverage_end": "2026-09-17",
                "query_status": "COMPLETE_WINDOW",
                "financial_documents": 10,
                "parsed_documents": 10,
                "soft_data_insufficient_documents": 0,
            },
            {
                "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
                "entity_id": "300001.SZ",
                "coverage_start": "2022-01-04",
                "coverage_end": "2026-09-17",
                "query_status": "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY",
                "financial_documents": 10,
                "parsed_documents": 9,
                "soft_data_insufficient_documents": 1,
            },
        ]
    )


def test_audit_computes_entity_coverage_and_revision_ambiguity() -> None:
    module = _module()
    errors = pd.DataFrame(
        [
            {
                "entity_id": "300001.SZ",
                "document_id": "x",
                "error": "missing extended line item",
                "severity": "SOFT_DATA_INSUFFICIENCY",
            }
        ]
    )
    fields, ambiguity, summary = module.audit_extended_pit_coverage(
        scope=_scope(),
        facts=_facts(),
        coverage=_coverage(),
        errors=errors,
    )

    money = fields.loc[fields["fact_type"].eq("MONETARY_FUNDS")].iloc[0]
    assert money["entities"] == 2
    assert money["entity_coverage_ratio"] == pytest.approx(1.0)
    assert money["same_timestamp_multi_revision_groups"] == 0
    assert money["formal_state"] == (
        "COMPLETE_ENTITY_COVERAGE_RAW_PIT_NOT_FORMALLY_QUALIFIED"
    )

    capex = fields.loc[fields["fact_type"].eq("CAPEX_CASH_PAID")].iloc[0]
    assert capex["entities"] == 1
    assert capex["same_timestamp_multi_revision_groups"] == 1
    assert capex["value_conflict_groups"] == 1
    assert capex["formal_state"] == (
        "PARTIAL_COVERAGE_DATA_INSUFFICIENT_NOT_QUALIFIED"
    )

    assert len(ambiguity) == 1
    assert ambiguity.iloc[0]["formal_resolution_state"] == (
        "AMBIGUOUS_NO_SOURCE_NATIVE_SEQUENCE"
    )
    assert summary["status"] == (
        "OUTCOME_BLIND_RAW_PIT_COVERAGE_AUDIT_COMPLETE_NOT_QUALIFIED"
    )
    assert summary["expected_entities"] == 2
    assert summary["soft_data_insufficiency_rows"] == 1
    assert summary["hard_failure_rows"] == 0
    assert summary["evidence_qualification_changed"] is False
    assert summary["outcome_read"] is False


def test_audit_fails_status_on_missing_scope_coverage() -> None:
    module = _module()
    fields, ambiguity, summary = module.audit_extended_pit_coverage(
        scope=_scope(),
        facts=_facts().loc[lambda x: x["entity_id"].eq("688001.SH")].copy(),
        coverage=_coverage().loc[lambda x: x["entity_id"].eq("688001.SH")].copy(),
        errors=pd.DataFrame(columns=["entity_id", "document_id", "error", "severity"]),
    )
    assert summary["status"] == "MATERIALIZATION_HARD_FAILURE"
    assert summary["missing_coverage_entities"] == ["300001.SZ"]
    assert summary["evidence_qualification_changed"] is False
    assert fields["formal_state"].str.contains("NOT_QUALIFIED").all()
    assert len(ambiguity) == 1


def test_audit_rejects_out_of_scope_facts() -> None:
    module = _module()
    facts = _facts().copy()
    facts.loc[0, "entity_id"] = "600000.SH"
    with pytest.raises(ValueError, match="out-of-scope"):
        module.audit_extended_pit_coverage(
            scope=_scope(),
            facts=facts,
            coverage=_coverage(),
            errors=pd.DataFrame(columns=["entity_id", "document_id", "error", "severity"]),
        )
