from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .innovation_drug_sector_kpi_raw_v1 import EVENT_COLUMNS
from .innovation_drug_cde_nmpa_snapshot_v1 import (
    CdeSnapshotMaterialization,
    load_cde_snapshot_contract,
    materialize_cde_snapshot,
    validate_cde_snapshot_manifest,
)

MAPPING_REGISTRY_ID = "INNOVATION_DRUG_CDE_NMPA_ENTITY_MAPPING_V1"


@dataclass(frozen=True)
class OfficialIntakeResult:
    materialization: CdeSnapshotMaterialization
    normalized_rows: pd.DataFrame
    unmapped_rows: pd.DataFrame
    duplicate_rows: pd.DataFrame
    summary: dict[str, object]


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_exact_entity_mapping_registry(path: Path) -> dict[str, object]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("registry_id") != MAPPING_REGISTRY_ID:
        raise ValueError("CDE/NMPA entity mapping registry identity mismatch")
    if registry.get("status") != "FROZEN_EXACT_MAPPING_ONLY":
        raise ValueError("CDE/NMPA entity mapping registry status mismatch")
    for key in ("fuzzy_matching_allowed", "substring_matching_allowed", "affiliate_inference_allowed"):
        if registry.get(key) is not False:
            raise ValueError(f"unsafe CDE/NMPA mapping registry flag: {key}")
    seen: set[str] = set()
    for item in registry.get("mappings", []):
        name = _text(item.get("applicant_name_exact"))
        if not name or name in seen:
            raise ValueError("CDE/NMPA exact mapping names must be unique and non-empty")
        seen.add(name)
        if item.get("mapping_basis") not in {
            "EXACT_LISTED_ISSUER_LEGAL_NAME",
            "EXACT_APPLICANT_ALIAS_REGISTRY",
            "EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE",
        }:
            raise ValueError("CDE/NMPA mapping basis is not exact/auditable")
        if not _text(item.get("entity_id")):
            raise ValueError("CDE/NMPA exact mapping requires entity_id")
        evidence = item.get("evidence") or {}
        if not _text(evidence.get("source_url")):
            raise ValueError("CDE/NMPA exact mapping requires evidence source_url")
    return registry


def _catalog(contract: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(item["url"]): dict(item) for item in contract["official_source_catalog"]}


def _validate_capture_status(manifest: dict[str, object], contract: dict[str, object]) -> dict[tuple[str, str], str]:
    allowed = set(contract["source_completeness"]["allowed_status"])
    rows = manifest.get("category_capture_status")
    if not isinstance(rows, list) or not rows:
        raise ValueError("CDE/NMPA manifest requires category_capture_status")
    out: dict[tuple[str, str], str] = {}
    catalog = _catalog(contract)
    for row in rows:
        url = _text(row.get("source_url"))
        category = _text(row.get("category"))
        status = _text(row.get("status"))
        if url not in catalog or category not in set(map(str, catalog[url]["allowed_categories"])):
            raise ValueError("CDE/NMPA category capture status references unregistered source/category")
        if status not in allowed:
            raise ValueError("CDE/NMPA category capture status is not allowed")
        key = (url, category)
        if key in out:
            raise ValueError("duplicate CDE/NMPA category capture status")
        out[key] = status
    frozen_targets = set(map(str, contract["canonical_event_types"]))
    required_pairs = {
        (url, category)
        for url, source in catalog.items()
        for category in map(str, source["allowed_categories"])
        if category in frozen_targets
    }
    missing_pairs = required_pairs - set(out)
    if missing_pairs:
        raise ValueError(
            "CDE/NMPA manifest missing frozen target category capture status: "
            + repr(sorted(missing_pairs))
        )
    return out


def _record_id(row: pd.Series, source: dict[str, object]) -> str:
    rule = str(source["record_identity_rule"])
    if rule == "ACCEPTANCE_NUMBER_REQUIRED":
        acceptance_no = _text(row.get("acceptance_no"))
        if not acceptance_no:
            raise ValueError("CDE/NMPA record requires acceptance_no")
        return acceptance_no
    if rule == "DRUG_HOLDER_APPROVAL_DATE_HASH":
        payload = {
            "source_url": _text(row.get("source_url")),
            "category": _text(row.get("category")),
            "drug_name": _text(row.get("drug_name")),
            "applicant": _text(row.get("applicant")),
            "approval_date": _text(row.get("approval_date")),
        }
        if not payload["drug_name"] or not payload["applicant"] or not payload["approval_date"]:
            raise ValueError("conditional approval identity requires drug_name/applicant/approval_date")
        return "conditional:" + _stable_hash(payload)
    raise ValueError("unsupported CDE/NMPA record identity rule")


def _dedupe_source_rows(
    frame: pd.DataFrame,
    *,
    duplicate_rows: list[dict[str, object]],
) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    identity = ["source_url", "category", "record_id"]
    keep_indexes: list[int] = []
    for key, group in frame.groupby(identity, sort=False, dropna=False):
        if len(group) == 1:
            keep_indexes.append(int(group.index[0]))
            continue
        compare_cols = [c for c in frame.columns if c != "mapping_evidence"]
        canonical = group[compare_cols].fillna("").astype(str).drop_duplicates()
        if len(canonical) != 1:
            raise ValueError(f"conflicting CDE/NMPA rows share source identity: {key}")
        keep_indexes.append(int(group.index[0]))
        for idx in group.index[1:]:
            duplicate_rows.append(
                {**group.loc[idx].to_dict(), "duplicate_state": "IDENTICAL_DUPLICATE_REMOVED"}
            )
    return frame.loc[keep_indexes].reset_index(drop=True)


def normalize_official_capture_rows(
    raw: pd.DataFrame,
    *,
    manifest: dict[str, object],
    contract: dict[str, object],
    mapping_registry: dict[str, object],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    validate_cde_snapshot_manifest(manifest, contract=contract)
    capture_status = _validate_capture_status(manifest, contract)
    required = set(contract["raw_capture_schema"]["required_columns"])
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"CDE/NMPA raw capture missing columns: {sorted(missing)}")
    if raw.empty:
        raise ValueError("CDE/NMPA raw official capture is empty")

    catalog = _catalog(contract)
    manifest_urls = set(map(str, manifest["source_urls"]))
    mapping = {item["applicant_name_exact"]: item for item in mapping_registry.get("mappings", [])}
    normalized: list[dict[str, object]] = []
    unmapped: list[dict[str, object]] = []

    for _, row in raw.iterrows():
        source_url = _text(row.get("source_url"))
        if source_url not in catalog or source_url not in manifest_urls:
            raise ValueError("CDE/NMPA raw row source URL is not registered by manifest/contract")
        source = catalog[source_url]
        category = _text(row.get("category"))
        if category not in set(map(str, source["allowed_categories"])):
            raise ValueError("CDE/NMPA raw row category does not belong to source page")
        if (source_url, category) not in capture_status:
            raise ValueError("CDE/NMPA raw row category has no declared capture status")

        semantics = str(source["availability_semantics"])
        if semantics == "OFFICIAL_PUBLICATION_DATE":
            if not _text(row.get("publication_date")):
                raise ValueError("CDE/NMPA historical publication row missing publication_date")
            availability_basis = "PUBLICATION_DATE_EXPLICIT"
        elif semantics == "OFFICIAL_APPROVAL_DATE":
            if not _text(row.get("approval_date")):
                raise ValueError("CDE/NMPA historical approval row missing approval_date")
            availability_basis = "OFFICIAL_APPROVAL_DATE_EXPLICIT"
        elif semantics == "FIRST_OBSERVED_SNAPSHOT_ONLY":
            if _text(row.get("publication_date")) or _text(row.get("approval_date")):
                raise ValueError("first-observed CDE/NMPA source cannot infer historical official date")
            availability_basis = "FIRST_OBSERVED_SNAPSHOT_DATE"
        else:
            raise ValueError("unsupported CDE/NMPA availability semantics")

        applicant = _text(row.get("applicant"))
        rid = _record_id(row, source)
        base = {
            "category": category,
            "record_id": rid,
            "applicant": applicant,
            "drug_name": _text(row.get("drug_name")),
            "indication": _text(row.get("indication")),
            "source_url": source_url,
            "availability_basis": availability_basis,
            "publication_date": _text(row.get("publication_date")),
            "approval_date": _text(row.get("approval_date")),
            "snapshot_captured_at": (
                str(manifest["captured_at"])
                if availability_basis == "FIRST_OBSERVED_SNAPSHOT_DATE"
                else ""
            ),
            "acceptance_no": _text(row.get("acceptance_no")),
            "registration_class": _text(row.get("registration_class")),
            "status": _text(row.get("status")),
            "capture_status": capture_status[(source_url, category)],
        }
        mapped = mapping.get(applicant)
        if mapped is None:
            unmapped.append({**base, "mapping_state": "UNMAPPED_EXACT_NAME_REQUIRED"})
            continue
        normalized.append(
            {
                **base,
                "entity_id": str(mapped["entity_id"]),
                "entity_mapping_basis": str(mapped["mapping_basis"]),
                "mapping_evidence": json.dumps(mapped["evidence"], ensure_ascii=False, sort_keys=True),
            }
        )

    mapped_frame = pd.DataFrame(normalized)
    unmapped_frame = pd.DataFrame(unmapped)
    duplicate_rows: list[dict[str, object]] = []
    mapped_frame = _dedupe_source_rows(mapped_frame, duplicate_rows=duplicate_rows)
    unmapped_frame = _dedupe_source_rows(unmapped_frame, duplicate_rows=duplicate_rows)
    duplicate_frame = pd.DataFrame(duplicate_rows)
    stats = {
        "raw_rows": int(len(raw)),
        "exact_mapped_rows": int(len(mapped_frame)),
        "unmapped_rows": int(len(unmapped_frame)),
        "identical_duplicate_rows_removed": int(len(duplicate_frame)),
        "capture_status_complete_categories": sum(v == "COMPLETE" for v in capture_status.values()),
        "capture_status_partial_categories": sum(v == "PARTIAL" for v in capture_status.values()),
        "source_completeness_inferred": False,
    }
    return mapped_frame, unmapped_frame, duplicate_frame, stats


def materialize_official_capture(
    raw: pd.DataFrame,
    *,
    manifest: dict[str, object],
    contract: dict[str, object],
    mapping_registry: dict[str, object],
    trading_dates: Iterable[object],
) -> OfficialIntakeResult:
    mapped, unmapped, duplicates, stats = normalize_official_capture_rows(
        raw,
        manifest=manifest,
        contract=contract,
        mapping_registry=mapping_registry,
    )
    if mapped.empty:
        events = pd.DataFrame(columns=EVENT_COLUMNS)
        materialized = CdeSnapshotMaterialization(
            events=events,
            summary={
                "mapped_event_rows": 0,
                "historical_reconstructable_rows": 0,
                "prospective_first_observed_rows": 0,
                "formal_sector_kpi_state": "DATA_INSUFFICIENT",
            },
        )
    else:
        materialized = materialize_cde_snapshot(
            mapped,
            manifest=manifest,
            contract=contract,
            trading_dates=trading_dates,
        )
    summary = {
        **stats,
        **materialized.summary,
        "state": "CDE_NMPA_OFFICIAL_RAW_CONTEXT_AVAILABLE",
        "source_identity": "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE",
        "cninfo_provenance_merged": False,
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "event_identity_is_direction": False,
        "event_count_as_score_allowed": False,
        "event_weight_defined": False,
        "sector_score_defined": False,
        "formal_sector_kpi_state": "DATA_INSUFFICIENT",
        "evidence_qualification_changed": False,
        "production_permission": "NONE",
        "trading_authority": False,
    }
    return OfficialIntakeResult(materialized, mapped, unmapped, duplicates, summary)


def materialize_official_capture_files(
    *,
    raw_snapshot_csv: Path,
    manifest_json: Path,
    contract_json: Path,
    mapping_registry_json: Path,
    trading_calendar_csv: Path,
    output_dir: Path,
) -> OfficialIntakeResult:
    contract = load_cde_snapshot_contract(contract_json)
    registry = load_exact_entity_mapping_registry(mapping_registry_json)
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    expected_sha = str(manifest.get("entity_mapping_registry_sha256") or "")
    actual_sha = _sha256(mapping_registry_json)
    if expected_sha != actual_sha:
        raise ValueError("CDE/NMPA manifest mapping registry SHA256 mismatch")
    raw = pd.read_csv(raw_snapshot_csv, dtype=str, keep_default_na=False)
    calendar = pd.read_csv(trading_calendar_csv)
    if "trade_date" not in calendar.columns:
        raise ValueError("trading calendar must contain trade_date")
    result = materialize_official_capture(
        raw,
        manifest=manifest,
        contract=contract,
        mapping_registry=registry,
        trading_dates=pd.to_datetime(calendar["trade_date"], errors="raise"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result.normalized_rows.to_csv(output_dir / "cde_nmpa_exact_mapped_snapshot_rows.csv", index=False)
    result.unmapped_rows.to_csv(output_dir / "cde_nmpa_unmapped_official_rows.csv", index=False)
    result.duplicate_rows.to_csv(output_dir / "cde_nmpa_duplicate_rows.csv", index=False)
    result.materialization.events.to_csv(output_dir / "cde_nmpa_mapped_raw_events.csv", index=False, date_format="%Y-%m-%d")
    summary = dict(result.summary)
    summary["input_sha256"] = {
        "raw_snapshot_csv": _sha256(raw_snapshot_csv),
        "manifest_json": _sha256(manifest_json),
        "contract_json": _sha256(contract_json),
        "mapping_registry_json": actual_sha,
        "trading_calendar_csv": _sha256(trading_calendar_csv),
    }
    (output_dir / "cde_nmpa_official_intake_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return OfficialIntakeResult(
        result.materialization,
        result.normalized_rows,
        result.unmapped_rows,
        result.duplicate_rows,
        summary,
    )


__all__ = [
    "MAPPING_REGISTRY_ID",
    "OfficialIntakeResult",
    "load_exact_entity_mapping_registry",
    "materialize_official_capture",
    "materialize_official_capture_files",
    "normalize_official_capture_rows",
]
