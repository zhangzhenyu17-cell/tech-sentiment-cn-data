import importlib.util
from pathlib import Path

import pandas as pd


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_v4a_derived_pit.py"
_SPEC = importlib.util.spec_from_file_location("v4a_derived_tolerance_test_module", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_fundamental_readiness_allows_explicit_row_level_data_insufficiency():
    evidence = pd.DataFrame(
        [
            {
                "entity_id": "600000.SH",
                "evidence_available_date": "2025-04-30",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
            },
            {
                "entity_id": "000001.SZ",
                "evidence_available_date": "2025-04-30",
                "availability_state": "DATA_INSUFFICIENT",
            },
        ]
    )
    filing_coverage = pd.DataFrame(
        [
            {"entity_id": "600000.SH", "query_status": "COMPLETE_WINDOW"},
            {"entity_id": "000001.SZ", "query_status": "COMPLETE_WINDOW"},
        ]
    )
    state, summary = _MODULE._fundamental_readiness(
        evidence,
        target_start=pd.Timestamp("2025-01-01"),
        target_end=pd.Timestamp("2025-12-31"),
        expected_entities={"600000.SH", "000001.SZ"},
        filing_coverage=filing_coverage,
    )
    assert state == "QUALIFIED_INPUT"
    assert summary["data_insufficient_records"] == 1
    assert summary["missing_accounted_entities"] == []


def test_fundamental_readiness_accounts_for_symbol_with_only_soft_filing_insufficiency():
    evidence = pd.DataFrame(
        [
            {
                "entity_id": "600000.SH",
                "evidence_available_date": "2025-04-30",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
            }
        ]
    )
    filing_coverage = pd.DataFrame(
        [
            {"entity_id": "600000.SH", "query_status": "COMPLETE_WINDOW"},
            {
                "entity_id": "000001.SZ",
                "query_status": "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY",
            },
        ]
    )
    state, summary = _MODULE._fundamental_readiness(
        evidence,
        target_start=pd.Timestamp("2025-01-01"),
        target_end=pd.Timestamp("2025-12-31"),
        expected_entities={"600000.SH", "000001.SZ"},
        filing_coverage=filing_coverage,
    )
    assert state == "QUALIFIED_INPUT"
    assert summary["accounted_entities"] == 2


def test_fundamental_readiness_rejects_unaccounted_symbol():
    evidence = pd.DataFrame(
        [
            {
                "entity_id": "600000.SH",
                "evidence_available_date": "2025-04-30",
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
            }
        ]
    )
    filing_coverage = pd.DataFrame(
        [{"entity_id": "600000.SH", "query_status": "COMPLETE_WINDOW"}]
    )
    state, summary = _MODULE._fundamental_readiness(
        evidence,
        target_start=pd.Timestamp("2025-01-01"),
        target_end=pd.Timestamp("2025-12-31"),
        expected_entities={"600000.SH", "000001.SZ"},
        filing_coverage=filing_coverage,
    )
    assert state == "PARTIAL_COVERAGE"
    assert summary["missing_accounted_entities"] == ["000001.SZ"]


def test_valuation_readiness_allows_missing_rows_without_emitting_fake_evidence():
    rail = pd.DataFrame(
        [
            {
                "date": "2025-06-02",
                "valuation_reference_date_20d": "2025-05-05",
                "trailing_pe": 20.0,
                "trailing_pe_20d_reference": 18.0,
                "valuation_change_20d": 20.0 / 18.0 - 1.0,
                "denominator_document_id": "doc1",
            },
            {
                "date": "2025-06-03",
                "valuation_reference_date_20d": "2025-05-06",
                "trailing_pe": float("nan"),
                "trailing_pe_20d_reference": float("nan"),
                "valuation_change_20d": float("nan"),
                "denominator_document_id": None,
            },
        ]
    )
    state, summary = _MODULE._valuation_readiness(
        rail,
        target_start=pd.Timestamp("2025-01-01"),
        target_end=pd.Timestamp("2025-12-31"),
    )
    assert state == "QUALIFIED_INPUT"
    assert summary["usable_rows"] == 1
    assert summary["row_level_data_insufficient_rows"] == 1


def test_valuation_readiness_still_requires_some_reconstructable_rows():
    rail = pd.DataFrame(
        [
            {
                "date": "2025-06-03",
                "valuation_reference_date_20d": "2025-05-06",
                "trailing_pe": float("nan"),
                "trailing_pe_20d_reference": float("nan"),
                "valuation_change_20d": float("nan"),
                "denominator_document_id": None,
            }
        ]
    )
    state, _ = _MODULE._valuation_readiness(
        rail,
        target_start=pd.Timestamp("2025-01-01"),
        target_end=pd.Timestamp("2025-12-31"),
    )
    assert state == "DATA_INSUFFICIENT"
