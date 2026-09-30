from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
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


def _single_line_text(value: object) -> str:
    """Canonicalize derived display text without mutating the raw official snapshot."""
    return re.sub(r"\s+", " ", _text(value)).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


APPLICANT_TOKEN_SPLIT_RE = re.compile(r"[;；\n、]+")
EXACT_MULTI_APPLICANT_TOKEN_BASIS = "EXACT_APPLICANT_TOKEN_IN_OFFICIAL_MULTI_APPLICANT_FIELD"


BASE_CAPTURE_OUTPUT_COLUMNS = (
    "category", "record_id", "applicant", "drug_name", "indication", "source_url",
    "availability_basis", "publication_date", "approval_date", "snapshot_captured_at",
    "acceptance_no", "registration_class", "status", "capture_status", "source_record_id",
)
MAPPED_CAPTURE_OUTPUT_COLUMNS = BASE_CAPTURE_OUTPUT_COLUMNS + (
    "entity_id", "entity_mapping_basis", "mapping_match_mode", "matched_applicant_token",
    "mapping_evidence",
)
UNMAPPED_CAPTURE_OUTPUT_COLUMNS = BASE_CAPTURE_OUTPUT_COLUMNS + ("mapping_state",)
DUPLICATE_CAPTURE_OUTPUT_COLUMNS = MAPPED_CAPTURE_OUTPUT_COLUMNS + ("mapping_state", "duplicate_state")


def _exact_mapping_matches(
    applicant: str, mapping: dict[str, dict[str, object]]
) -> list[tuple[dict[str, object], str, str]]:
    whole = mapping.get(applicant)
    if whole is not None:
        return [(whole, str(whole["mapping_basis"]), applicant)]
    tokens: list[str] = []
    for token in APPLICANT_TOKEN_SPLIT_RE.split(applicant):
        value = token.strip()
        if value and value not in tokens:
            tokens.append(value)
    matches = [(mapping[token], EXACT_MULTI_APPLICANT_TOKEN_BASIS, token) for token in tokens if token in mapping]
    out: list[tuple[dict[str, object], str, str]] = []
    seen_entities: set[str] = set()
    for mapped, basis, token in matches:
        entity_id = str(mapped["entity_id"])
        if entity_id in seen_entities:
            continue
        seen_entities.add(entity_id)
        out.append((mapped, basis, token))
    return out


def _exact_mapping_match(
    applicant: str, mapping: dict[str, dict[str, object]]
) -> tuple[dict[str, object] | None, str | None, str | None]:
    matches = _exact_mapping_matches(applicant, mapping)
    if len(matches) > 1:
        raise ValueError(
            "CDE/NMPA official multi-applicant row maps to multiple listed entities; "
            "fan-out semantics must be explicitly implemented before intake"
        )
    if not matches:
        return None, None, None
    return matches[0]


def build_official_capture_manifest(
    *,
    contract: dict[str, object],
    mapping_registry_sha256: str,
    captured_at: str,
    capture_status: str = "PARTIAL",
    snapshot_id: str | None = None,
    capture_query_company: str | None = None,
) -> dict[str, object]:
    allowed = set(contract["source_completeness"]["allowed_status"])
    if capture_status not in allowed:
        raise ValueError("CDE/NMPA capture status is not allowed")
    timestamp = pd.Timestamp(captured_at)
    if timestamp.tzinfo is None:
        raise ValueError("CDE/NMPA captured_at must be timezone-aware")
    captured_iso = timestamp.isoformat()
    canonical = set(map(str, contract["canonical_event_types"]))
    source_urls: list[str] = []
    category_rows: list[dict[str, str]] = []
    for source in contract["official_source_catalog"]:
        url = str(source["url"])
        source_urls.append(url)
        for category in map(str, source["allowed_categories"]):
            if category in canonical:
                category_rows.append(
                    {"source_url": url, "category": category, "status": capture_status}
                )
    if not category_rows:
        raise ValueError("CDE/NMPA contract exposes no canonical capture categories")
    if snapshot_id is None:
        stamp = timestamp.tz_convert("Asia/Shanghai").strftime("%Y%m%dT%H%M%S%z")
        snapshot_id = f"innovation-drug-cde-nmpa-{stamp}"
    payload: dict[str, object] = {
        "manifest_id": "INNOVATION_DRUG_CDE_NMPA_SNAPSHOT_CAPTURE_V1",
        "snapshot_id": snapshot_id,
        "source_identity": "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE",
        "captured_at": captured_iso,
        "capture_method": "BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE",
        "source_urls": source_urls,
        "category_capture_status": category_rows,
        "entity_mapping_registry_sha256": mapping_registry_sha256,
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
    if capture_query_company:
        payload["capture_scope"] = {
            "kind": "OFFICIAL_COMPANY_QUERY",
            "company_query": str(capture_query_company),
            "completeness_semantics": "QUERY_SCOPED_EXHAUSTIVE_PAGINATION",
            "full_source_category_completeness_claimed": False,
        }
    return payload


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
    if rule == "OFFICIAL_ROW_CODE_PREFERRED_ACCEPTANCE_NUMBER_FALLBACK":
        source_record_id = _text(row.get("source_record_id"))
        if source_record_id:
            return "official:" + source_record_id
        acceptance_no = _text(row.get("acceptance_no"))
        if not acceptance_no:
            raise ValueError(
                "CDE/NMPA record requires source_record_id or acceptance_no"
            )
        return acceptance_no
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
    if "entity_id" in frame.columns:
        identity.append("entity_id")
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
    allow_multi_entity_fanout: bool = False,
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
            "drug_name": _single_line_text(row.get("drug_name")),
            "indication": _single_line_text(row.get("indication")),
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
            "registration_class": _single_line_text(row.get("registration_class")),
            "status": _single_line_text(row.get("status")),
            "capture_status": capture_status[(source_url, category)],
            "source_record_id": _text(row.get("source_record_id")),
        }
        matches = _exact_mapping_matches(applicant, mapping)
        if not matches:
            unmapped.append({**base, "mapping_state": "UNMAPPED_EXACT_NAME_OR_TOKEN_REQUIRED"})
            continue
        if len(matches) > 1 and not allow_multi_entity_fanout:
            raise ValueError(
                "CDE/NMPA official multi-applicant row maps to multiple listed entities; "
                "fan-out semantics must be explicitly implemented before intake"
            )
        for mapped, mapping_basis, matched_token in matches:
            match_mode = (
                "WHOLE_FIELD_EXACT"
                if applicant == matched_token
                else "DELIMITED_APPLICANT_TOKEN_EXACT"
            )
            mapping_evidence = {
                "registry_evidence": mapped["evidence"],
                "mapping_match_mode": match_mode,
                "matched_applicant_token": matched_token,
                "official_applicant_field": applicant,
                "multi_entity_fanout_enabled": bool(allow_multi_entity_fanout),
                "multi_entity_match_count": len(matches),
                "substring_matching_used": False,
                "affiliate_inference_used": False,
            }
            normalized.append(
                {
                    **base,
                    "entity_id": str(mapped["entity_id"]),
                    "entity_mapping_basis": str(mapping_basis),
                    "mapping_match_mode": match_mode,
                    "matched_applicant_token": matched_token,
                    "mapping_evidence": json.dumps(mapping_evidence, ensure_ascii=False, sort_keys=True),
                }
            )

    mapped_frame = pd.DataFrame(normalized, columns=MAPPED_CAPTURE_OUTPUT_COLUMNS)
    unmapped_frame = pd.DataFrame(unmapped, columns=UNMAPPED_CAPTURE_OUTPUT_COLUMNS)
    duplicate_rows: list[dict[str, object]] = []
    mapped_frame = _dedupe_source_rows(mapped_frame, duplicate_rows=duplicate_rows)
    unmapped_frame = _dedupe_source_rows(unmapped_frame, duplicate_rows=duplicate_rows)
    duplicate_frame = pd.DataFrame(duplicate_rows, columns=DUPLICATE_CAPTURE_OUTPUT_COLUMNS)
    stats = {
        "raw_rows": int(len(raw)),
        "exact_mapped_rows": int(len(mapped_frame)),
        "whole_field_exact_mapped_rows": int(
            mapped_frame.get("mapping_match_mode", pd.Series(dtype=str)).eq("WHOLE_FIELD_EXACT").sum()
        ),
        "delimited_token_exact_mapped_rows": int(
            mapped_frame.get("mapping_match_mode", pd.Series(dtype=str)).eq("DELIMITED_APPLICANT_TOKEN_EXACT").sum()
        ),
        "unmapped_rows": int(len(unmapped_frame)),
        "identical_duplicate_rows_removed": int(len(duplicate_frame)),
        "capture_status_complete_categories": sum(v == "COMPLETE" for v in capture_status.values()),
        "capture_status_partial_categories": sum(v == "PARTIAL" for v in capture_status.values()),
        "source_completeness_inferred": False,
        "multi_entity_fanout_enabled": bool(allow_multi_entity_fanout),
        "multi_entity_fanout_source_rows": int(
            mapped_frame.groupby(["source_url", "category", "record_id"], dropna=False)["entity_id"]
            .nunique()
            .gt(1)
            .sum()
        ) if not mapped_frame.empty else 0,
    }
    return mapped_frame, unmapped_frame, duplicate_frame, stats


def materialize_official_capture(
    raw: pd.DataFrame,
    *,
    manifest: dict[str, object],
    contract: dict[str, object],
    mapping_registry: dict[str, object],
    trading_dates: Iterable[object],
    allow_multi_entity_fanout: bool = False,
) -> OfficialIntakeResult:
    mapped, unmapped, duplicates, stats = normalize_official_capture_rows(
        raw,
        manifest=manifest,
        contract=contract,
        mapping_registry=mapping_registry,
        allow_multi_entity_fanout=allow_multi_entity_fanout,
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
        "capture_scope": manifest.get("capture_scope"),
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
    "APPLICANT_TOKEN_SPLIT_RE",
    "EXACT_MULTI_APPLICANT_TOKEN_BASIS",
    "MAPPING_REGISTRY_ID",
    "OfficialIntakeResult",
    "build_official_capture_manifest",
    "load_exact_entity_mapping_registry",
    "materialize_official_capture",
    "materialize_official_capture_files",
    "normalize_official_capture_rows",
]
