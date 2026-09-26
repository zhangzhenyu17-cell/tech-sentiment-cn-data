import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_cde_nmpa_official_intake_v1 import (
    load_exact_entity_mapping_registry,
    materialize_official_capture,
    materialize_official_capture_files,
    normalize_official_capture_rows,
)
from tech_sentiment.innovation_drug_cde_nmpa_snapshot_v1 import load_cde_snapshot_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/innovation_drug_cde_nmpa_snapshot_v1_contract.json"
MAPPING = ROOT / "reference/innovation_drug_cde_nmpa_entity_mapping_v1.json"

URL_PRIORITY = "https://www.cde.org.cn/main/xxgk/listpage/2f78f372d351c6851af7431c7710a731"
URL_BREAKTHROUGH = "https://www.cde.org.cn/main/xxgk/listpage/da6efd086c099b7fc949121166f0130c"
URL_IMPLIED = "https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c"
URL_CONDITIONAL = "https://www.cde.org.cn/main/xxgk/listpage/c8d79e513a6df98adf05893281ace198"


def _manifest(mapping_sha: str = "a" * 64) -> dict[str, object]:
    return {
        "manifest_id": "INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_CAPTURE_V1",
        "snapshot_id": "official-capture-test-1",
        "source_identity": "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE",
        "captured_at": "2026-09-26T09:30:00+08:00",
        "capture_method": "BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE",
        "source_urls": [URL_PRIORITY, URL_BREAKTHROUGH, URL_IMPLIED, URL_CONDITIONAL],
        "category_capture_status": [
            {"source_url": URL_PRIORITY, "category": "纳入优先审评品种名单", "status": "COMPLETE"},
            {"source_url": URL_BREAKTHROUGH, "category": "纳入突破性治疗品种名单", "status": "COMPLETE"},
            {"source_url": URL_IMPLIED, "category": "临床试验默示许可", "status": "COMPLETE"},
            {"source_url": URL_CONDITIONAL, "category": "附条件批准品种", "status": "COMPLETE"},
        ],
        "entity_mapping_registry_sha256": mapping_sha,
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


def _raw_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "category": "纳入优先审评品种名单",
            "source_url": URL_PRIORITY,
            "applicant": "江苏恒瑞医药股份有限公司",
            "drug_name": "HR-PRIORITY",
            "acceptance_no": "CXHS2600001",
            "indication": "示例适应症A",
            "publication_date": "2026-09-24",
        },
        {
            "category": "纳入突破性治疗品种名单",
            "source_url": URL_BREAKTHROUGH,
            "applicant": "江苏恒瑞医药股份有限公司",
            "drug_name": "HR-BREAKTHROUGH",
            "acceptance_no": "CXHL2600002",
            "indication": "示例适应症B",
            "publication_date": "2026-09-25",
        },
        {
            "category": "临床试验默示许可",
            "source_url": URL_IMPLIED,
            "applicant": "江苏恒瑞医药股份有限公司",
            "drug_name": "HR-IMPLIED",
            "acceptance_no": "CXHL2600003",
            "indication": "示例适应症C",
        },
        {
            "category": "附条件批准品种",
            "source_url": URL_CONDITIONAL,
            "applicant": "江苏恒瑞医药股份有限公司",
            "drug_name": "HR-CONDITIONAL",
            "indication": "示例适应症D",
            "approval_date": "2026-09-23",
            "status": "有效",
        },
        {
            "category": "纳入优先审评品种名单",
            "source_url": URL_PRIORITY,
            "applicant": "江苏恒瑞生物科技有限公司",
            "drug_name": "UNMAPPED-AFFILIATE",
            "acceptance_no": "CXHL2600999",
            "indication": "示例适应症E",
            "publication_date": "2026-09-25",
        },
    ])


def test_exact_mapping_registry_is_narrow_and_auditable() -> None:
    registry = load_exact_entity_mapping_registry(MAPPING)
    assert registry["fuzzy_matching_allowed"] is False
    assert registry["affiliate_inference_allowed"] is False
    mappings = registry["mappings"]
    assert [x["applicant_name_exact"] for x in mappings] == ["江苏恒瑞医药股份有限公司"]
    assert mappings[0]["entity_id"] == "600276.SH"
    assert mappings[0]["mapping_basis"] == "EXACT_LISTED_ISSUER_LEGAL_NAME"


def test_raw_official_capture_maps_exact_name_preserves_unmapped_and_pit_semantics() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    registry = load_exact_entity_mapping_registry(MAPPING)
    result = materialize_official_capture(
        _raw_rows(),
        manifest=_manifest(),
        contract=contract,
        mapping_registry=registry,
        trading_dates=pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]),
    )
    assert result.summary["state"] == "CDE_NMPA_OFFICIAL_RAW_CONTEXT_AVAILABLE"
    assert result.summary["exact_mapped_rows"] == 4
    assert result.summary["unmapped_rows"] == 1
    assert result.summary["historical_reconstructable_rows"] == 3
    assert result.summary["prospective_first_observed_rows"] == 1
    assert result.summary["capture_status_complete_categories"] == 4
    assert result.summary["source_completeness_inferred"] is False
    assert result.summary["formal_sector_kpi_state"] == "DATA_INSUFFICIENT"
    assert result.summary["historical_outcomes_read"] is False
    assert result.summary["sector_score_defined"] is False
    assert set(result.materialization.events["event_type"]) == {
        "CDE_PRIORITY_REVIEW_INCLUDED",
        "CDE_BREAKTHROUGH_THERAPY_INCLUDED",
        "CDE_CLINICAL_TRIAL_IMPLIED_LICENSE_SNAPSHOT",
        "CDE_CONDITIONAL_APPROVAL",
    }
    conditional = result.materialization.events.query("event_type == 'CDE_CONDITIONAL_APPROVAL'").iloc[0]
    provenance = json.loads(conditional["provenance"])
    assert provenance["approval_date"] == "2026-09-23"
    assert provenance["publication_date"] is None
    implied = result.materialization.events.query("event_type == 'CDE_CLINICAL_TRIAL_IMPLIED_LICENSE_SNAPSHOT'").iloc[0]
    assert implied["availability_state"] == "PROSPECTIVE_FIRST_OBSERVED_ONLY"
    assert str(implied["event_date"].date()) == "2026-09-26"
    assert str(implied["evidence_available_date"].date()) == "2026-09-28"
    assert result.unmapped_rows.iloc[0]["applicant"] == "江苏恒瑞生物科技有限公司"
    assert result.unmapped_rows.iloc[0]["mapping_state"] == "UNMAPPED_EXACT_NAME_REQUIRED"


def test_identical_duplicate_is_reported_but_conflicting_revision_fails_closed() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    registry = load_exact_entity_mapping_registry(MAPPING)
    row = _raw_rows().iloc[[0]].copy()
    raw = pd.concat([row, row], ignore_index=True)
    mapped, unmapped, duplicates, stats = normalize_official_capture_rows(
        raw, manifest=_manifest(), contract=contract, mapping_registry=registry
    )
    assert len(mapped) == 1
    assert unmapped.empty
    assert len(duplicates) == 1
    assert stats["identical_duplicate_rows_removed"] == 1

    conflict = raw.copy()
    conflict.loc[1, "drug_name"] = "DIFFERENT-NAME"
    with pytest.raises(ValueError, match="conflicting CDE/NMPA rows"):
        normalize_official_capture_rows(
            conflict, manifest=_manifest(), contract=contract, mapping_registry=registry
        )


def test_first_observed_source_rejects_historical_date_backfill() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    registry = load_exact_entity_mapping_registry(MAPPING)
    row = _raw_rows().iloc[[2]].copy()
    row["publication_date"] = "2024-01-01"
    with pytest.raises(ValueError, match="cannot infer historical official date"):
        normalize_official_capture_rows(
            row, manifest=_manifest(), contract=contract, mapping_registry=registry
        )


def test_file_materializer_binds_exact_mapping_registry_sha_and_writes_audits(tmp_path: Path) -> None:
    mapping_sha = hashlib.sha256(MAPPING.read_bytes()).hexdigest()
    raw_path = tmp_path / "raw.csv"
    manifest_path = tmp_path / "manifest.json"
    calendar_path = tmp_path / "calendar.csv"
    out = tmp_path / "out"
    _raw_rows().to_csv(raw_path, index=False)
    manifest_path.write_text(json.dumps(_manifest(mapping_sha), ensure_ascii=False), encoding="utf-8")
    pd.DataFrame({"trade_date": ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]}).to_csv(calendar_path, index=False)
    result = materialize_official_capture_files(
        raw_snapshot_csv=raw_path,
        manifest_json=manifest_path,
        contract_json=CONTRACT,
        mapping_registry_json=MAPPING,
        trading_calendar_csv=calendar_path,
        output_dir=out,
    )
    assert result.summary["exact_mapped_rows"] == 4
    assert (out / "cde_nmpa_exact_mapped_snapshot_rows.csv").exists()
    assert (out / "cde_nmpa_unmapped_official_rows.csv").exists()
    assert (out / "cde_nmpa_duplicate_rows.csv").exists()
    assert (out / "cde_nmpa_mapped_raw_events.csv").exists()
    assert (out / "cde_nmpa_official_intake_summary.json").exists()

    bad = _manifest("0" * 64)
    manifest_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="mapping registry SHA256 mismatch"):
        materialize_official_capture_files(
            raw_snapshot_csv=raw_path,
            manifest_json=manifest_path,
            contract_json=CONTRACT,
            mapping_registry_json=MAPPING,
            trading_calendar_csv=calendar_path,
            output_dir=out,
        )


def test_contract_freezes_official_endpoint_identity_without_requests_scraper() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    acquisition = contract["acquisition"]
    assert acquisition["direct_requests_scraper_required"] is False
    assert acquisition["waf_bypass_allowed"] is False
    assert acquisition["endpoint_transport_state"] == (
        "OFFICIAL_ENDPOINTS_IDENTIFIED_BROWSER_WAF_SESSION_REQUIRED_NO_DIRECT_REQUESTS_SCRAPER"
    )
    catalog = {x["category_family"]: x for x in contract["official_source_catalog"]}
    assert catalog["PRIORITY_REVIEW"]["official_api_path"] == "/priority/getPriorityApprovalList"
    assert catalog["PRIORITY_REVIEW"]["request_params"]["noticeType"] == 2
    assert catalog["BREAKTHROUGH_THERAPY"]["official_api_path"] == "/breakthrough/getBreakthroughCureList"
    assert catalog["BREAKTHROUGH_THERAPY"]["request_params"]["noticeType"] == 1
    assert catalog["IMPLIED_CLINICAL_TRIAL_LICENSE"]["official_api_path"] == "/xxgk/getCliniCalList"
    conditional = catalog["CONDITIONAL_APPROVAL"]
    assert conditional["official_api_path"] == "/xxgk/getFtjpzqdList"
    assert conditional["approval_date_source_field"] == "list[].bcftjpzDate"


def test_source_probe_and_public_governance_remain_fail_closed() -> None:
    probe = json.loads((ROOT / "reference/innovation_drug_cde_nmpa_source_probe_v1.json").read_text())
    assert probe["state"] == "OFFICIAL_ENDPOINTS_IDENTIFIED_REAL_SNAPSHOT_NOT_MATERIALIZED"
    assert probe["data_state"]["real_official_snapshot_materialized"] is False
    assert probe["data_state"]["historical_reconstructable_rows"] == 0
    assert probe["data_state"]["prospective_first_observed_rows"] == 0
    assert probe["data_state"]["formal_sector_kpi_state"] == "DATA_INSUFFICIENT"
    assert probe["governance"]["historical_outcome_read"] is False
    assert probe["governance"]["production_permission"] == "NONE"
    assert probe["governance"]["trading_authority"] is False

    governance = json.loads((ROOT / "reference/public_data_governance_control_plane_v1.json").read_text())
    product = next(
        x for x in governance["registered_products"]
        if x["product_id"] == "INNOVATION_DRUG_CDE_NMPA_OFFICIAL_RAW_CONTEXT_V1"
    )
    assert product["producer_path"] == "scripts/materialize_innovation_drug_cde_nmpa_official_intake_v1.py"
    assert product["public_workflow_success_grants_private_qualification"] is False
    assert product["may_promote_evidence"] is False


def test_unmapped_source_duplicates_are_audited_and_conflicts_fail_closed() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    registry = load_exact_entity_mapping_registry(MAPPING)
    row = _raw_rows().iloc[[4]].copy()
    raw = pd.concat([row, row], ignore_index=True)
    mapped, unmapped, duplicates, stats = normalize_official_capture_rows(
        raw, manifest=_manifest(), contract=contract, mapping_registry=registry
    )
    assert mapped.empty
    assert len(unmapped) == 1
    assert len(duplicates) == 1
    assert stats["unmapped_rows"] == 1
    assert stats["identical_duplicate_rows_removed"] == 1

    conflict = raw.copy()
    conflict.loc[1, "drug_name"] = "UNMAPPED-CONFLICT"
    with pytest.raises(ValueError, match="conflicting CDE/NMPA rows"):
        normalize_official_capture_rows(
            conflict, manifest=_manifest(), contract=contract, mapping_registry=registry
        )


def test_manifest_must_declare_all_frozen_target_category_capture_states() -> None:
    contract = load_cde_snapshot_contract(CONTRACT)
    registry = load_exact_entity_mapping_registry(MAPPING)
    manifest = _manifest()
    manifest["category_capture_status"] = manifest["category_capture_status"][:-1]
    with pytest.raises(ValueError, match="missing frozen target category capture status"):
        normalize_official_capture_rows(
            _raw_rows().iloc[[0]],
            manifest=manifest,
            contract=contract,
            mapping_registry=registry,
        )
