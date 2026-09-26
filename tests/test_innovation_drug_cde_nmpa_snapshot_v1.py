import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_cde_nmpa_snapshot_v1 import (
    load_cde_snapshot_contract,
    materialize_cde_snapshot,
    materialize_cde_snapshot_files,
    validate_cde_snapshot_manifest,
    validate_cde_snapshot_rows,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "reference/innovation_drug_cde_nmpa_snapshot_v1_contract.json"


def _manifest(*urls: str) -> dict[str, object]:
    return {
        "manifest_id": "INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_CAPTURE_V1",
        "snapshot_id": "snapshot-test-1",
        "source_identity": "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE",
        "captured_at": "2026-09-26T09:30:00+08:00",
        "capture_method": "BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE",
        "source_urls": list(urls),
        "entity_mapping_registry_sha256": "a" * 64,
        "historical_outcome_read": False,
        "prospective_outcome_read": False,
        "fuzzy_entity_mapping_used": False,
        "publication_date_inferred": False,
        "current_page_presence_used_for_historical_backfill": False,
        "current_constituent_backfill_used": False,
        "direction_classified": False,
        "predictive_weight_assigned": False,
        "sector_score_computed": False,
        "evidence_qualification_changed": False,
        "formal_sector_kpi_state": "DATA_INSUFFICIENT",
    }


def _priority_row() -> dict[str, object]:
    return {
        "entity_id": "600276.SH",
        "entity_mapping_basis": "EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE",
        "category": "纳入优先审评品种名单",
        "availability_basis": "PUBLICATION_DATE_EXPLICIT",
        "publication_date": "2026-09-25",
        "snapshot_captured_at": "",
        "record_id": "priority-1",
        "applicant": "江苏恒瑞医药股份有限公司",
        "drug_name": "TEST-001",
        "indication": "示例适应症",
        "source_url": (
            "https://www.cde.org.cn/main/xxgk/listpage/"
            "2f78f372d351c6851af7431c7710a731"
        ),
    }


def _implied_permission_row() -> dict[str, object]:
    return {
        "entity_id": "600276.SH",
        "entity_mapping_basis": "EXACT_APPLICANT_ALIAS_REGISTRY",
        "category": "临床试验默示许可",
        "availability_basis": "FIRST_OBSERVED_SNAPSHOT_DATE",
        "publication_date": "",
        "snapshot_captured_at": "2026-09-26T09:30:00+08:00",
        "record_id": "implicit-1",
        "applicant": "江苏恒瑞医药股份有限公司",
        "drug_name": "TEST-IND-001",
        "indication": "示例适应症",
        "source_url": (
            "https://www.cde.org.cn/main/xxgk/listpage/"
            "4b5255eb0a84820cef4ca3e8b6bbe20c"
        ),
    }


def test_contract_is_outcome_blind_and_fail_closed() -> None:
    contract = load_cde_snapshot_contract(CONTRACT_PATH)
    assert contract["status"] == "FROZEN_OUTCOME_BLIND_OFFICIAL_SNAPSHOT_ADAPTER_READY_DATA_PENDING"
    assert contract["pit_semantics"]["missing_publication_date_may_be_inferred"] is False
    assert contract["pit_semantics"]["current_page_presence_may_imply_historical_presence"] is False
    assert contract["pit_semantics"]["first_observed_snapshot_historical_backfill_allowed"] is False
    assert contract["pit_semantics"]["fuzzy_entity_mapping_allowed"] is False
    assert contract["interpretation_boundary"]["sector_score_defined"] is False
    assert contract["interpretation_boundary"]["formal_sector_kpi_state"] == "DATA_INSUFFICIENT"
    assert contract["authority"]["historical_outcome_read"] is False
    assert contract["authority"]["prospective_outcome_read"] is False
    assert contract["authority"]["production_permission"] == "NONE"
    assert contract["authority"]["trading_authority"] is False


def test_explicit_date_page_requires_explicit_publication_date() -> None:
    contract = load_cde_snapshot_contract(CONTRACT_PATH)
    row = _priority_row()
    row["availability_basis"] = "FIRST_OBSERVED_SNAPSHOT_DATE"
    row["publication_date"] = ""
    row["snapshot_captured_at"] = "2026-09-26"
    snapshot = pd.DataFrame([row])
    manifest = _manifest(str(row["source_url"]))
    validate_cde_snapshot_manifest(manifest, contract=contract)
    with pytest.raises(ValueError, match="requires availability basis PUBLICATION_DATE_EXPLICIT"):
        validate_cde_snapshot_rows(snapshot, contract=contract, manifest=manifest)


def test_page_without_record_date_is_prospective_first_observed_only() -> None:
    contract = load_cde_snapshot_contract(CONTRACT_PATH)
    row = _implied_permission_row()
    snapshot = pd.DataFrame([row])
    manifest = _manifest(str(row["source_url"]))
    calendar = pd.to_datetime(["2026-09-25", "2026-09-28", "2026-09-29"])

    result = materialize_cde_snapshot(
        snapshot,
        manifest=manifest,
        contract=contract,
        trading_dates=calendar,
    )
    assert result.summary["mapped_event_rows"] == 1
    assert result.summary["historical_reconstructable_rows"] == 0
    assert result.summary["prospective_first_observed_rows"] == 1
    assert result.summary["event_identity_is_direction"] is False
    assert result.summary["event_count_as_score_allowed"] is False
    assert result.summary["formal_sector_kpi_state"] == "DATA_INSUFFICIENT"
    assert result.events.iloc[0]["availability_state"] == "PROSPECTIVE_FIRST_OBSERVED_ONLY"
    assert str(result.events.iloc[0]["evidence_available_date"].date()) == "2026-09-28"


def test_mixed_official_snapshot_preserves_availability_semantics(tmp_path: Path) -> None:
    contract = load_cde_snapshot_contract(CONTRACT_PATH)
    rows = [_priority_row(), _implied_permission_row()]
    urls = [str(row["source_url"]) for row in rows]
    manifest = _manifest(*urls)
    snapshot = pd.DataFrame(rows)
    calendar = pd.to_datetime(["2026-09-25", "2026-09-28", "2026-09-29"])

    result = materialize_cde_snapshot(
        snapshot,
        manifest=manifest,
        contract=contract,
        trading_dates=calendar,
    )
    assert result.summary["mapped_event_rows"] == 2
    assert result.summary["historical_reconstructable_rows"] == 1
    assert result.summary["prospective_first_observed_rows"] == 1
    assert set(result.events["availability_state"]) == {
        "HISTORICAL_RECONSTRUCTABLE",
        "PROSPECTIVE_FIRST_OBSERVED_ONLY",
    }

    snapshot_path = tmp_path / "snapshot.csv"
    manifest_path = tmp_path / "manifest.json"
    calendar_path = tmp_path / "calendar.csv"
    out = tmp_path / "out"
    snapshot.to_csv(snapshot_path, index=False)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pd.DataFrame({"trade_date": calendar}).to_csv(calendar_path, index=False)

    file_result = materialize_cde_snapshot_files(
        snapshot_csv=snapshot_path,
        manifest_json=manifest_path,
        contract_json=CONTRACT_PATH,
        trading_calendar_csv=calendar_path,
        output_dir=out,
    )
    assert len(file_result.events) == 2
    assert (out / "cde_nmpa_mapped_raw_events.csv").exists()
    summary = json.loads((out / "cde_nmpa_snapshot_summary.json").read_text())
    assert len(summary["input_sha256"]["snapshot_csv"]) == 64
    assert summary["sector_score_defined"] is False
    assert summary["production_permission"] == "NONE"


def test_manifest_rejects_unregistered_source_and_unsafe_semantics() -> None:
    contract = load_cde_snapshot_contract(CONTRACT_PATH)
    bad = _manifest("https://www.cde.org.cn/main/xxgk/listpage/not-registered")
    with pytest.raises(ValueError, match="unregistered source URL"):
        validate_cde_snapshot_manifest(bad, contract=contract)

    unsafe = _manifest(
        "https://www.cde.org.cn/main/xxgk/listpage/"
        "4b5255eb0a84820cef4ca3e8b6bbe20c"
    )
    unsafe["publication_date_inferred"] = True
    with pytest.raises(ValueError, match="publication_date_inferred"):
        validate_cde_snapshot_manifest(unsafe, contract=contract)
