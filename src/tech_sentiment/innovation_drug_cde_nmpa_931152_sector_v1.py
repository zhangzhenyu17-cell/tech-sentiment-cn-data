from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .innovation_drug_931152_issuer_identity_v1 import (
    EXPECTED_MEMBER_COUNT,
    MAPPING_REGISTRY_ID,
    REGISTRY_ID as ISSUER_REGISTRY_ID,
)
from .innovation_drug_cde_nmpa_official_intake_v1 import (
    build_official_capture_manifest,
    materialize_official_capture,
)
from .innovation_drug_cde_nmpa_snapshot_v1 import load_cde_snapshot_contract
from .innovation_drug_sector_kpi_raw_v1 import (
    CDE_SOURCE_ID,
    filter_events_to_pit_membership,
    validate_931152_membership_scope,
)

SECTOR_SNAPSHOT_ID = "INNOVATION_DRUG_CDE_NMPA_931152_QUERY_SET_V1"
SECTOR_STATE = "931152_QUERY_SET_CDE_RAW_SNAPSHOT_MATERIALIZED_NOT_SCORED"
PIT_STATE = "931152_PIT_MEMBER_FILTERED_CDE_RAW_CONTEXT_AVAILABLE_NOT_SCORED"


@dataclass(frozen=True)
class SectorCdeMaterialization:
    all_events: pd.DataFrame
    pit_events: pd.DataFrame
    excluded_events: pd.DataFrame
    normalized_rows: pd.DataFrame
    unmapped_rows: pd.DataFrame
    duplicate_rows: pd.DataFrame
    summary: dict[str, object]
    manifest: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_931152_mapping_registry(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("registry_id") != MAPPING_REGISTRY_ID:
        raise ValueError("931152 CDE mapping registry identity mismatch")
    if payload.get("status") != "FROZEN_931152_EXACT_LISTED_ISSUER_MAPPING_ONLY":
        raise ValueError("931152 CDE mapping registry status mismatch")
    if str(payload.get("index_code")) != "931152":
        raise ValueError("931152 CDE mapping registry index mismatch")
    if int(payload.get("membership_unique_symbols", -1)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE mapping registry member count drift")
    for key in ("fuzzy_matching_allowed", "substring_matching_allowed", "affiliate_inference_allowed"):
        if payload.get(key) is not False:
            raise ValueError(f"unsafe 931152 CDE mapping flag: {key}")
    if payload.get("multi_applicant_exact_token_matching_allowed") is not True:
        raise ValueError("931152 CDE mapping requires exact multi-applicant token matching")
    if payload.get("multi_entity_exact_fanout_allowed") is not True:
        raise ValueError("931152 CDE mapping requires explicit multi-entity fanout")
    mappings = list(payload.get("mappings") or [])
    if len(mappings) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE mapping rows must equal frozen member union")
    names = [str(x.get("applicant_name_exact") or "").strip() for x in mappings]
    entities = [str(x.get("entity_id") or "").strip() for x in mappings]
    if any(not x for x in names) or len(set(names)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE legal-name mappings must be non-empty and unique")
    if any(not x for x in entities) or len(set(entities)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE entity mappings must be non-empty and unique")
    for item in mappings:
        if item.get("mapping_basis") != "EXACT_LISTED_ISSUER_LEGAL_NAME":
            raise ValueError("931152 v1 CDE mapping basis must remain exact listed issuer legal name")
        evidence = item.get("evidence") or {}
        if not str(evidence.get("source_url") or "").strip():
            raise ValueError("931152 CDE mapping requires official exchange evidence")
    safety = payload.get("safety") or {}
    for key in (
        "fuzzy_matching_used", "substring_matching_used", "affiliate_inference_used",
        "outcome_read", "direction_classified", "predictive_weight_assigned",
        "sector_score_defined", "evidence_qualification_changed", "trading_authority",
    ):
        if safety.get(key) is not False:
            raise ValueError(f"931152 CDE mapping safety drift: {key}")
    if safety.get("production_permission") != "NONE":
        raise ValueError("931152 CDE mapping cannot grant Production")
    return payload


def validate_issuer_registry(path: Path, *, membership: pd.DataFrame) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("registry_id") != ISSUER_REGISTRY_ID:
        raise ValueError("931152 issuer registry identity mismatch")
    if payload.get("status") != "OFFICIAL_EXCHANGE_IDENTITY_RESOLVED_EXACT_ONLY":
        raise ValueError("931152 issuer registry status mismatch")
    if int(payload.get("resolved_issuer_rows", -1)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 issuer registry resolution incomplete")
    if int(payload.get("unresolved_issuer_rows", -1)) != 0:
        raise ValueError("931152 issuer registry contains unresolved rows")
    symbols = {str(x.get("symbol") or "").zfill(6) for x in payload.get("issuers", [])}
    expected = set(validate_931152_membership_scope(membership)["symbol"].astype(str))
    if symbols != expected:
        raise ValueError("931152 issuer registry symbol set differs from PIT membership union")
    return payload


def build_sector_manifest(
    *,
    contract: dict[str, object],
    mapping_registry_sha256: str,
    captured_at: str,
    membership_scope_sha256: str,
    issuer_identity_registry_sha256: str,
    query_receipt_sha256: str,
) -> dict[str, object]:
    manifest = build_official_capture_manifest(
        contract=contract,
        mapping_registry_sha256=mapping_registry_sha256,
        captured_at=captured_at,
        capture_status="COMPLETE",
        snapshot_id=f"innovation-drug-cde-nmpa-931152-{pd.Timestamp(captured_at).tz_convert('Asia/Shanghai').strftime('%Y%m%dT%H%M%S%z')}",
    )
    manifest["capture_scope"] = {
        "kind": "OFFICIAL_931152_FROZEN_MEMBER_LISTED_ISSUER_QUERY_SET",
        "index_code": "931152",
        "membership_interval_rows": 155,
        "membership_unique_symbols": EXPECTED_MEMBER_COUNT,
        "company_query_count": EXPECTED_MEMBER_COUNT,
        "query_identity": "CURRENT_OFFICIAL_LISTED_ISSUER_LEGAL_NAME_EXACT",
        "completeness_semantics": "ALL_FROZEN_MEMBER_COMPANY_QUERIES_EXHAUSTIVE_PAGINATION",
        "historical_legal_name_alias_completeness_claimed": False,
        "affiliate_completeness_claimed": False,
        "full_source_category_completeness_claimed": False,
    }
    manifest["membership_scope_sha256"] = membership_scope_sha256
    manifest["issuer_identity_registry_sha256"] = issuer_identity_registry_sha256
    manifest["query_receipt_sha256"] = query_receipt_sha256
    manifest["multi_entity_exact_fanout_enabled"] = True
    return manifest


def _excluded_with_reason(all_events: pd.DataFrame, pit_events: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    if all_events.empty:
        out = all_events.copy()
        out["pit_exclusion_reason"] = pd.Series(dtype=object)
        return out
    included = set(pit_events.get("event_id", pd.Series(dtype=str)).astype(str))
    excluded = all_events.loc[~all_events["event_id"].astype(str).isin(included)].copy()
    scope = validate_931152_membership_scope(membership)
    max_scope = pd.Timestamp(scope["effective_end"].max()).normalize()
    available = pd.to_datetime(excluded["evidence_available_date"], errors="raise").dt.normalize()
    excluded["pit_exclusion_reason"] = [
        "AFTER_QUALIFIED_931152_MEMBERSHIP_SCOPE_END"
        if date > max_scope
        else "ENTITY_NOT_ACTIVE_931152_MEMBER_AT_EVIDENCE_DATE"
        for date in available
    ]
    return excluded.reset_index(drop=True)


def materialize_931152_sector_cde(
    *,
    raw: pd.DataFrame,
    query_receipt: dict[str, object],
    contract: dict[str, object],
    mapping_registry: dict[str, object],
    membership: pd.DataFrame,
    trading_dates: Iterable[object],
    manifest: dict[str, object],
) -> SectorCdeMaterialization:
    if query_receipt.get("receipt_id") != "INNOVATION_DRUG_CDE_NMPA_931152_QUERY_RECEIPT_V1":
        raise ValueError("931152 CDE query receipt identity mismatch")
    if int(query_receipt.get("company_query_count", -1)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE company query count incomplete")
    if int(query_receipt.get("complete_company_query_count", -1)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 CDE company queries are not all complete")
    if int(query_receipt.get("query_error_count", -1)) != 0:
        raise ValueError("931152 CDE query receipt contains errors")
    if query_receipt.get("all_source_categories_complete") is not True:
        raise ValueError("931152 CDE query set source-category coverage incomplete")
    if query_receipt.get("full_source_category_completeness_claimed") is not False:
        raise ValueError("931152 listed-issuer query set cannot claim full CDE category completeness")
    scope = validate_931152_membership_scope(membership)
    result = materialize_official_capture(
        raw,
        manifest=manifest,
        contract=contract,
        mapping_registry=mapping_registry,
        trading_dates=trading_dates,
        allow_multi_entity_fanout=True,
    )
    all_events = result.materialization.events
    pit_events = filter_events_to_pit_membership(all_events, scope)
    excluded = _excluded_with_reason(all_events, pit_events, scope)
    all_available = pd.to_datetime(all_events["evidence_available_date"], errors="raise") if len(all_events) else pd.Series(dtype="datetime64[ns]")
    pit_available = pd.to_datetime(pit_events["evidence_available_date"], errors="raise") if len(pit_events) else pd.Series(dtype="datetime64[ns]")
    excluded_counts = (
        {str(k): int(v) for k, v in excluded["pit_exclusion_reason"].value_counts().sort_index().items()}
        if len(excluded) else {}
    )
    query_rows = list(query_receipt.get("queries") or [])
    zero_query_count = sum(int(row.get("raw_rows", 0)) == 0 for row in query_rows)
    summary = {
        "summary_id": "INNOVATION_DRUG_CDE_NMPA_931152_SECTOR_V1_SUMMARY",
        "state": SECTOR_STATE,
        "pit_member_filtered_state": PIT_STATE,
        "date": "2026-09-30",
        "domain_id": "INNOVATION_DRUG",
        "index_code": "931152",
        "membership_scope_start": str(scope["effective_start"].min().date()),
        "membership_scope_end": str(scope["effective_end"].max().date()),
        "membership_interval_rows": int(len(scope)),
        "membership_unique_symbols": int(scope["symbol"].nunique()),
        "company_query_count": int(query_receipt["company_query_count"]),
        "complete_company_query_count": int(query_receipt["complete_company_query_count"]),
        "zero_result_company_query_count": int(zero_query_count),
        "raw_query_rows_before_source_dedup": int(len(raw)),
        "exact_mapped_projection_rows": int(len(result.normalized_rows)),
        "unmapped_source_rows": int(len(result.unmapped_rows)),
        "identical_duplicate_projection_rows_removed": int(len(result.duplicate_rows)),
        "multi_entity_fanout_source_rows": int(result.summary.get("multi_entity_fanout_source_rows", 0)),
        "all_mapped_event_rows": int(len(all_events)),
        "historical_reconstructable_rows_total": int(result.summary.get("historical_reconstructable_rows", 0)),
        "prospective_first_observed_rows_total": int(result.summary.get("prospective_first_observed_rows", 0)),
        "pit_member_cde_event_rows_through_qualified_scope": int(len(pit_events)),
        "pit_member_cde_entities_with_events": int(pit_events["entity_id"].nunique()) if len(pit_events) else 0,
        "pit_member_cde_earliest_evidence_available_date": None if not len(pit_events) else str(pit_available.min().date()),
        "pit_member_cde_latest_evidence_available_date": None if not len(pit_events) else str(pit_available.max().date()),
        "excluded_event_rows": int(len(excluded)),
        "excluded_rows_by_reason": excluded_counts,
        "all_events_latest_evidence_available_date": None if not len(all_events) else str(all_available.max().date()),
        "capture_scope": manifest["capture_scope"],
        "source_identity": CDE_SOURCE_ID,
        "source_event_rows_may_be_interpreted_as_unique_economic_events": False,
        "cross_source_cninfo_cde_deduplication_applied": False,
        "current_constituent_backfill_used": False,
        "historical_legal_name_alias_completeness_claimed": False,
        "affiliate_completeness_claimed": False,
        "full_source_category_completeness_claimed": False,
        "event_identity_is_direction": False,
        "event_count_as_score_allowed": False,
        "event_weight_defined": False,
        "sector_score_defined": False,
        "company_score_defined": False,
        "formal_sector_kpi_state": "DATA_INSUFFICIENT",
        "sector_kpi_qualified": False,
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "evidence_qualification_changed": False,
        "production_permission": "NONE",
        "trading_authority": False,
        "automatic_execution": False,
    }
    return SectorCdeMaterialization(
        all_events=all_events,
        pit_events=pit_events,
        excluded_events=excluded,
        normalized_rows=result.normalized_rows,
        unmapped_rows=result.unmapped_rows,
        duplicate_rows=result.duplicate_rows,
        summary=summary,
        manifest=manifest,
    )


def materialize_931152_sector_cde_files(
    *,
    raw_snapshot_csv: Path,
    query_receipt_json: Path,
    contract_json: Path,
    mapping_registry_json: Path,
    issuer_registry_json: Path,
    membership_scope_csv: Path,
    trading_calendar_csv: Path,
    output_dir: Path,
) -> SectorCdeMaterialization:
    raw = pd.read_csv(raw_snapshot_csv, dtype=str, keep_default_na=False)
    query_receipt = json.loads(query_receipt_json.read_text(encoding="utf-8"))
    contract = load_cde_snapshot_contract(contract_json)
    membership = pd.read_csv(membership_scope_csv, dtype={"symbol": str})
    issuer_registry = validate_issuer_registry(issuer_registry_json, membership=membership)
    mapping_registry = load_931152_mapping_registry(mapping_registry_json)
    if mapping_registry.get("membership_scope_sha256") != issuer_registry.get("membership_scope_sha256"):
        raise ValueError("931152 CDE mapping and issuer registry membership identity mismatch")
    calendar = pd.read_csv(trading_calendar_csv)
    if "trade_date" not in calendar.columns:
        raise ValueError("931152 CDE trading calendar requires trade_date")
    manifest = build_sector_manifest(
        contract=contract,
        mapping_registry_sha256=_sha256(mapping_registry_json),
        captured_at=str(query_receipt["captured_at"]),
        membership_scope_sha256=_sha256(membership_scope_csv),
        issuer_identity_registry_sha256=_sha256(issuer_registry_json),
        query_receipt_sha256=_sha256(query_receipt_json),
    )
    if manifest["membership_scope_sha256"] != issuer_registry.get("membership_scope_sha256"):
        raise ValueError("931152 CDE manifest membership scope SHA mismatch")
    result = materialize_931152_sector_cde(
        raw=raw,
        query_receipt=query_receipt,
        contract=contract,
        mapping_registry=mapping_registry,
        membership=membership,
        trading_dates=pd.to_datetime(calendar["trade_date"], errors="raise"),
        manifest=manifest,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result.normalized_rows.to_csv(output_dir / "cde_nmpa_931152_exact_mapped_projection_rows.csv", index=False)
    result.unmapped_rows.to_csv(output_dir / "cde_nmpa_931152_unmapped_source_rows.csv", index=False)
    result.duplicate_rows.to_csv(output_dir / "cde_nmpa_931152_duplicate_projection_rows.csv", index=False)
    result.all_events.to_csv(output_dir / "cde_nmpa_931152_all_mapped_raw_events.csv", index=False)
    result.pit_events.to_csv(output_dir / "cde_nmpa_931152_pit_member_raw_events.csv", index=False)
    result.excluded_events.to_csv(output_dir / "cde_nmpa_931152_pit_excluded_raw_events.csv", index=False)
    (output_dir / "cde_nmpa_931152_snapshot_manifest.json").write_text(
        json.dumps(result.manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    summary = dict(result.summary)
    summary["input_sha256"] = {
        "raw_snapshot_csv": _sha256(raw_snapshot_csv),
        "query_receipt_json": _sha256(query_receipt_json),
        "contract_json": _sha256(contract_json),
        "mapping_registry_json": _sha256(mapping_registry_json),
        "issuer_registry_json": _sha256(issuer_registry_json),
        "membership_scope_csv": _sha256(membership_scope_csv),
        "trading_calendar_csv": _sha256(trading_calendar_csv),
    }
    (output_dir / "cde_nmpa_931152_sector_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return SectorCdeMaterialization(
        result.all_events, result.pit_events, result.excluded_events,
        result.normalized_rows, result.unmapped_rows, result.duplicate_rows,
        summary, result.manifest,
    )


__all__ = [
    "PIT_STATE",
    "SECTOR_SNAPSHOT_ID",
    "SECTOR_STATE",
    "SectorCdeMaterialization",
    "build_sector_manifest",
    "load_931152_mapping_registry",
    "materialize_931152_sector_cde",
    "materialize_931152_sector_cde_files",
    "validate_issuer_registry",
]
