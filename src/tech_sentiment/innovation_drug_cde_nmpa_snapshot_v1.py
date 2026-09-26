from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd

from .innovation_drug_sector_kpi_raw_v1 import (
    CDE_SOURCE_ID,
    DOMAIN_ID,
    normalize_cde_snapshot,
)

CONTRACT_ID = "INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_V1"
CONTRACT_STATUS = "FROZEN_OUTCOME_BLIND_OFFICIAL_SNAPSHOT_ADAPTER_READY_DATA_PENDING"
ADAPTER_VERSION = "innovation-drug-cde-nmpa-snapshot-adapter-v1"

_ALLOWED_CAPTURE_METHODS = {
    "OFFICIAL_EXPORT",
    "BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE",
}
_REQUIRED_MANIFEST_FALSE = {
    "historical_outcome_read",
    "prospective_outcome_read",
    "fuzzy_entity_mapping_used",
    "publication_date_inferred",
    "current_page_presence_used_for_historical_backfill",
    "current_constituent_backfill_used",
    "direction_classified",
    "predictive_weight_assigned",
    "sector_score_computed",
    "evidence_qualification_changed",
}


@dataclass(frozen=True)
class CdeSnapshotMaterialization:
    events: pd.DataFrame
    summary: dict[str, object]


def _china_local_date(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Shanghai").tz_localize(None)
    return timestamp.normalize()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cde_snapshot_contract(path: Path) -> dict[str, object]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("CDE/NMPA snapshot contract identity mismatch")
    if contract.get("status") != CONTRACT_STATUS:
        raise ValueError("CDE/NMPA snapshot contract status mismatch")
    if contract.get("domain_id") != DOMAIN_ID:
        raise ValueError("CDE/NMPA snapshot domain mismatch")
    if contract.get("source_identity") != CDE_SOURCE_ID:
        raise ValueError("CDE/NMPA source identity mismatch")
    if contract["pit_semantics"]["missing_publication_date_may_be_inferred"] is not False:
        raise ValueError("CDE/NMPA contract cannot infer missing publication dates")
    if contract["pit_semantics"]["current_page_presence_may_imply_historical_presence"] is not False:
        raise ValueError("CDE/NMPA contract cannot backfill from current page presence")
    if contract["pit_semantics"]["fuzzy_entity_mapping_allowed"] is not False:
        raise ValueError("CDE/NMPA contract cannot allow fuzzy entity mapping")
    if contract["interpretation_boundary"]["formal_sector_kpi_state"] != "DATA_INSUFFICIENT":
        raise ValueError("CDE/NMPA adapter cannot qualify formal sector KPI")
    for key in (
        "event_identity_is_direction",
        "supportive_or_adverse_state_defined",
        "event_weight_defined",
        "clinical_stage_weight_defined",
        "sector_score_defined",
        "company_score_defined",
        "absence_of_record_may_imply_negative_or_positive_state",
    ):
        if contract["interpretation_boundary"][key] is not False:
            raise ValueError(f"CDE/NMPA interpretation boundary violated: {key}")
    for key in (
        "historical_outcome_read",
        "prospective_outcome_read",
        "parameter_search",
        "threshold_search",
        "weight_search",
        "model_training",
        "evidence_qualification_changed",
        "trading_authority",
        "automatic_execution",
        "real_percentage_mapping",
    ):
        if contract["authority"][key] is not False:
            raise ValueError(f"CDE/NMPA authority boundary violated: {key}")
    if contract["authority"]["production_permission"] != "NONE":
        raise ValueError("CDE/NMPA adapter cannot grant Production")
    return contract


def _catalog_by_url(contract: dict[str, object]) -> dict[str, dict[str, object]]:
    catalog: dict[str, dict[str, object]] = {}
    for item in contract["official_source_catalog"]:
        url = str(item["url"])
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.cde.org.cn", "cde.org.cn"}:
            raise ValueError("CDE source catalog must use official CDE HTTPS URLs")
        if url in catalog:
            raise ValueError("duplicate CDE source catalog URL")
        catalog[url] = dict(item)
    if not catalog:
        raise ValueError("CDE source catalog is empty")
    return catalog


def validate_cde_snapshot_manifest(
    manifest: dict[str, object],
    *,
    contract: dict[str, object],
) -> None:
    if manifest.get("manifest_id") != "INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_CAPTURE_V1":
        raise ValueError("CDE/NMPA snapshot manifest identity mismatch")
    if manifest.get("source_identity") != CDE_SOURCE_ID:
        raise ValueError("CDE/NMPA snapshot manifest source identity mismatch")
    if manifest.get("capture_method") not in _ALLOWED_CAPTURE_METHODS:
        raise ValueError("unsupported CDE/NMPA snapshot capture method")
    captured_at = _china_local_date(manifest.get("captured_at"))
    if pd.isna(captured_at):
        raise ValueError("CDE/NMPA snapshot manifest captured_at is invalid")
    if not str(manifest.get("snapshot_id") or "").strip():
        raise ValueError("CDE/NMPA snapshot manifest requires snapshot_id")
    mapping_sha = str(manifest.get("entity_mapping_registry_sha256") or "")
    if len(mapping_sha) != 64 or any(ch not in "0123456789abcdef" for ch in mapping_sha.lower()):
        raise ValueError("CDE/NMPA snapshot manifest requires mapping registry SHA256")
    for key in _REQUIRED_MANIFEST_FALSE:
        if manifest.get(key) is not False:
            raise ValueError(f"CDE/NMPA snapshot manifest safety flag violated: {key}")
    if manifest.get("formal_sector_kpi_state") != "DATA_INSUFFICIENT":
        raise ValueError("CDE/NMPA snapshot manifest cannot qualify sector KPI")
    allowed_urls = set(_catalog_by_url(contract))
    observed_urls = {str(value) for value in manifest.get("source_urls", [])}
    if not observed_urls:
        raise ValueError("CDE/NMPA snapshot manifest requires source_urls")
    if not observed_urls.issubset(allowed_urls):
        raise ValueError("CDE/NMPA snapshot manifest contains unregistered source URL")


def validate_cde_snapshot_rows(
    snapshot: pd.DataFrame,
    *,
    contract: dict[str, object],
    manifest: dict[str, object],
) -> None:
    required = set(contract["snapshot_schema"]["required_identity_columns"])
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"CDE/NMPA snapshot rows missing columns: {sorted(missing)}")
    if snapshot.empty:
        raise ValueError("CDE/NMPA mapped snapshot contains no rows")

    catalog = _catalog_by_url(contract)
    manifest_urls = {str(value) for value in manifest["source_urls"]}
    for _, row in snapshot.iterrows():
        source_url = str(row["source_url"]).strip()
        if source_url not in manifest_urls or source_url not in catalog:
            raise ValueError("CDE/NMPA row source URL is not registered by manifest")
        source = catalog[source_url]
        category = str(row["category"]).strip()
        if category not in set(map(str, source["allowed_categories"])):
            raise ValueError("CDE/NMPA row category does not belong to source page")
        basis = str(row["availability_basis"]).strip()
        if basis not in set(contract["snapshot_schema"]["availability_basis_allowed"]):
            raise ValueError("CDE/NMPA row availability basis is not frozen")

        semantics = str(source.get("availability_semantics") or "")
        expected_basis = {
            "OFFICIAL_PUBLICATION_DATE": "PUBLICATION_DATE_EXPLICIT",
            "OFFICIAL_APPROVAL_DATE": "OFFICIAL_APPROVAL_DATE_EXPLICIT",
            "FIRST_OBSERVED_SNAPSHOT_ONLY": "FIRST_OBSERVED_SNAPSHOT_DATE",
        }.get(semantics)
        if expected_basis is None:
            expected_basis = (
                "PUBLICATION_DATE_EXPLICIT"
                if bool(source.get("record_level_publication_date_available"))
                else "FIRST_OBSERVED_SNAPSHOT_DATE"
            )
        if basis != expected_basis:
            raise ValueError(
                f"CDE/NMPA source requires availability basis {expected_basis}"
            )

        if basis in {"PUBLICATION_DATE_EXPLICIT", "OFFICIAL_APPROVAL_DATE_EXPLICIT"}:
            field = "publication_date" if basis == "PUBLICATION_DATE_EXPLICIT" else "approval_date"
            value = row.get(field)
            if pd.isna(value) or not str(value).strip():
                raise ValueError(f"explicit CDE/NMPA row missing {field}")
        else:
            value = row.get("snapshot_captured_at")
            if pd.isna(value) or not str(value).strip():
                raise ValueError("first-observed CDE/NMPA row missing snapshot_captured_at")
            row_capture = _china_local_date(value)
            manifest_capture = _china_local_date(manifest["captured_at"])
            if row_capture != manifest_capture:
                raise ValueError(
                    "first-observed CDE/NMPA row capture date must match manifest"
                )

        mapping_basis = str(row["entity_mapping_basis"]).strip()
        if mapping_basis not in {
            "EXACT_LISTED_ISSUER_LEGAL_NAME",
            "EXACT_APPLICANT_ALIAS_REGISTRY",
            "EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE",
        }:
            raise ValueError("CDE/NMPA row entity mapping must be exact and auditable")


def materialize_cde_snapshot(
    snapshot: pd.DataFrame,
    *,
    manifest: dict[str, object],
    contract: dict[str, object],
    trading_dates: Iterable[object],
) -> CdeSnapshotMaterialization:
    validate_cde_snapshot_manifest(manifest, contract=contract)
    validate_cde_snapshot_rows(snapshot, contract=contract, manifest=manifest)
    events = normalize_cde_snapshot(snapshot, trading_dates=trading_dates)
    historical_rows = int(
        events["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE").sum()
    )
    first_observed_rows = int(
        events["availability_state"].astype(str).eq("PROSPECTIVE_FIRST_OBSERVED_ONLY").sum()
    )
    summary = {
        "adapter_version": ADAPTER_VERSION,
        "contract_id": CONTRACT_ID,
        "domain_id": DOMAIN_ID,
        "source_identity": CDE_SOURCE_ID,
        "snapshot_id": str(manifest["snapshot_id"]),
        "captured_at": str(manifest["captured_at"]),
        "capture_method": str(manifest["capture_method"]),
        "mapped_event_rows": int(len(events)),
        "historical_reconstructable_rows": historical_rows,
        "prospective_first_observed_rows": first_observed_rows,
        "entities": sorted(set(events["entity_id"].astype(str))),
        "categories": sorted(
            {
                json.loads(value)["category"]
                for value in events["provenance"].astype(str)
            }
        ),
        "source_urls": sorted(set(map(str, manifest["source_urls"]))),
        "event_identity_is_direction": False,
        "event_count_as_score_allowed": False,
        "event_weight_defined": False,
        "sector_score_defined": False,
        "formal_sector_kpi_state": "DATA_INSUFFICIENT",
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "evidence_qualification_changed": False,
        "production_permission": "NONE",
        "trading_authority": False,
    }
    return CdeSnapshotMaterialization(events=events, summary=summary)


def materialize_cde_snapshot_files(
    *,
    snapshot_csv: Path,
    manifest_json: Path,
    contract_json: Path,
    trading_calendar_csv: Path,
    output_dir: Path,
) -> CdeSnapshotMaterialization:
    contract = load_cde_snapshot_contract(contract_json)
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    snapshot = pd.read_csv(snapshot_csv, dtype=str, keep_default_na=False)
    calendar = pd.read_csv(trading_calendar_csv)
    if "trade_date" not in calendar.columns:
        raise ValueError("trading calendar must contain trade_date")
    result = materialize_cde_snapshot(
        snapshot,
        manifest=manifest,
        contract=contract,
        trading_dates=pd.to_datetime(calendar["trade_date"], errors="raise"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result.events.to_csv(
        output_dir / "cde_nmpa_mapped_raw_events.csv",
        index=False,
        date_format="%Y-%m-%d",
    )
    summary = dict(result.summary)
    summary["input_sha256"] = {
        "snapshot_csv": _sha256(snapshot_csv),
        "manifest_json": _sha256(manifest_json),
        "contract_json": _sha256(contract_json),
        "trading_calendar_csv": _sha256(trading_calendar_csv),
    }
    (output_dir / "cde_nmpa_snapshot_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return CdeSnapshotMaterialization(events=result.events, summary=summary)


__all__ = [
    "ADAPTER_VERSION",
    "CONTRACT_ID",
    "CONTRACT_STATUS",
    "CdeSnapshotMaterialization",
    "load_cde_snapshot_contract",
    "materialize_cde_snapshot",
    "materialize_cde_snapshot_files",
    "validate_cde_snapshot_manifest",
    "validate_cde_snapshot_rows",
]
