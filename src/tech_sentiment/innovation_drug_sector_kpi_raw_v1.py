from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd

from .pit_public_materialization import (
    _market_available_date,
    _real_trading_calendar,
    _stable_hash,
)

SCHEMA_VERSION = "innovation-drug-sector-kpi-raw-v1"
DOMAIN_ID = "INNOVATION_DRUG"
CNINFO_SOURCE_ID = "CNINFO_ANNOUNCEMENT_ARCHIVE"
CDE_SOURCE_ID = "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE"

EVENT_COLUMNS = (
    "event_id",
    "entity_id",
    "domain_id",
    "event_family",
    "event_type",
    "event_date",
    "evidence_available_date",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "title",
    "source_url_identity",
    "classification_basis",
    "provenance",
    "ingestion_identity",
    "availability_state",
    "direction_classified",
    "predictive_weight_assigned",
    "outcome_read",
)

CLINICAL_REGULATORY = "CLINICAL_REGULATORY"
BD_LICENSING = "BD_LICENSING"

EVENT_FAMILY = {
    "CLINICAL_TRIAL_AUTHORIZATION": CLINICAL_REGULATORY,
    "CLINICAL_TRIAL_TERMINATION": CLINICAL_REGULATORY,
    "BREAKTHROUGH_THERAPY_DESIGNATION": CLINICAL_REGULATORY,
    "MARKETING_APPLICATION_ACCEPTED": CLINICAL_REGULATORY,
    "MARKETING_APPLICATION_PRIORITY_REVIEW": CLINICAL_REGULATORY,
    "MARKETING_APPROVAL": CLINICAL_REGULATORY,
    "REGISTRATION_APPLICATION_WITHDRAWAL": CLINICAL_REGULATORY,
    "CDE_BREAKTHROUGH_PROPOSED": CLINICAL_REGULATORY,
    "CDE_BREAKTHROUGH_INCLUDED": CLINICAL_REGULATORY,
    "CDE_PRIORITY_REVIEW_PROPOSED": CLINICAL_REGULATORY,
    "CDE_PRIORITY_REVIEW_INCLUDED": CLINICAL_REGULATORY,
    "CDE_IMPLIED_CLINICAL_TRIAL_PERMISSION": CLINICAL_REGULATORY,
    "CDE_CONDITIONAL_APPROVAL": CLINICAL_REGULATORY,
    "BD_LICENSE_OR_COLLABORATION": BD_LICENSING,
    "BD_COLLABORATION": BD_LICENSING,
}

_CDE_CATEGORY_MAP = {
    "拟突破性治疗品种": "CDE_BREAKTHROUGH_PROPOSED",
    "纳入突破性治疗品种名单": "CDE_BREAKTHROUGH_INCLUDED",
    "拟优先审评品种": "CDE_PRIORITY_REVIEW_PROPOSED",
    "纳入优先审评品种名单": "CDE_PRIORITY_REVIEW_INCLUDED",
    "临床试验默示许可": "CDE_IMPLIED_CLINICAL_TRIAL_PERMISSION",
    "附条件批准品种": "CDE_CONDITIONAL_APPROVAL",
}


@dataclass(frozen=True)
class SectorKpiRawResult:
    events: pd.DataFrame
    summary: dict[str, object]


def _clean_title(value: object) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def classify_innovation_drug_title(title: object) -> str | None:
    """Outcome-blind Innovation Drug event taxonomy from an issuer title only.

    The taxonomy identifies event identities; it does not classify an event as
    supportive/adverse, assign a score, or infer economic impact.
    """

    text = _clean_title(title)
    if not text:
        return None

    if any(token in text for token in ("终止临床", "临床试验终止", "临床失败")):
        return "CLINICAL_TRIAL_TERMINATION"
    if "撤回药品注册申请" in text:
        return "REGISTRATION_APPLICATION_WITHDRAWAL"
    if "突破性治疗" in text:
        return "BREAKTHROUGH_THERAPY_DESIGNATION"
    if "上市许可申请" in text and "优先审评" in text:
        return "MARKETING_APPLICATION_PRIORITY_REVIEW"
    if "上市许可申请" in text and any(token in text for token in ("受理", "获受理")):
        return "MARKETING_APPLICATION_ACCEPTED"
    if any(token in text for token in ("获得药品注册批准", "药品注册批准", "获批上市", "批准上市")):
        return "MARKETING_APPROVAL"
    if any(token in text for token in ("临床试验批准通知书", "临床试验批准")):
        return "CLINICAL_TRIAL_AUTHORIZATION"

    # Business-development licensing requires an agreement/collaboration phrase.
    # Bare "许可" or "授权" is intentionally insufficient because those tokens
    # also occur in regulatory marketing-authorization and corporate-governance titles.
    if any(
        token in text
        for token in ("许可协议", "授权协议", "独家许可", "合作及许可协议", "许可及合作协议")
    ):
        return "BD_LICENSE_OR_COLLABORATION"
    if "合作协议" in text and any(token in text for token in ("药", "治疗", "研发", "临床")):
        return "BD_COLLABORATION"
    return None


def _validate_event_frame(events: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in EVENT_COLUMNS if column not in events.columns]
    if missing:
        raise ValueError(f"sector KPI raw events missing columns: {missing}")
    out = events.copy().reset_index(drop=True)
    if out.empty:
        return out
    out["event_date"] = pd.to_datetime(out["event_date"], errors="raise").dt.normalize()
    out["evidence_available_date"] = pd.to_datetime(
        out["evidence_available_date"], errors="raise"
    ).dt.normalize()
    if (out["evidence_available_date"] < out["event_date"]).any():
        raise ValueError("sector KPI evidence_available_date cannot precede event_date")
    if out["event_id"].duplicated().any():
        raise ValueError("sector KPI event_id must be unique")
    if out["ingestion_identity"].duplicated().any():
        raise ValueError("sector KPI ingestion_identity must be unique")
    if not out["domain_id"].astype(str).eq(DOMAIN_ID).all():
        raise ValueError("sector KPI event domain drift")
    if out["direction_classified"].astype(bool).any():
        raise ValueError("raw sector KPI events cannot classify direction")
    if out["predictive_weight_assigned"].astype(bool).any():
        raise ValueError("raw sector KPI events cannot assign predictive weights")
    if out["outcome_read"].astype(bool).any():
        raise ValueError("raw sector KPI events cannot read outcomes")
    allowed_types = set(EVENT_FAMILY)
    bad_types = sorted(set(out["event_type"].astype(str)) - allowed_types)
    if bad_types:
        raise ValueError(f"unknown sector KPI event types: {bad_types}")
    for event_type, family in out[["event_type", "event_family"]].itertuples(index=False):
        if EVENT_FAMILY[str(event_type)] != str(family):
            raise ValueError("sector KPI event family/type mismatch")
    return out.sort_values(
        ["evidence_available_date", "entity_id", "event_id"]
    ).reset_index(drop=True)


def normalize_cninfo_sector_events(records: pd.DataFrame) -> pd.DataFrame:
    required = {
        "entity_id",
        "event_date",
        "evidence_available_date",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "ingestion_identity",
        "availability_state",
        "title",
        "source_url_identity",
    }
    missing = required - set(records.columns)
    if missing:
        raise ValueError(f"CNINFO PIT records missing columns: {sorted(missing)}")
    if not records.empty and set(records["source_identity"].astype(str)) != {CNINFO_SOURCE_ID}:
        raise ValueError("CNINFO sector event input source identity mismatch")

    rows: list[dict[str, object]] = []
    for _, item in records.iterrows():
        event_type = classify_innovation_drug_title(item["title"])
        if event_type is None:
            continue
        identity_payload = {
            "schema_version": SCHEMA_VERSION,
            "source_identity": CNINFO_SOURCE_ID,
            "entity_id": str(item["entity_id"]),
            "document_id": str(item["document_id"]),
            "revision_id": str(item["revision_id"]),
            "event_type": event_type,
            "evidence_available_date": str(pd.Timestamp(item["evidence_available_date"]).date()),
        }
        event_id = "idkpi:" + _stable_hash(identity_payload)
        provenance = {
            "source_record_provenance": json.loads(str(item["provenance"])),
            "source_ingestion_identity": str(item["ingestion_identity"]),
            "taxonomy": SCHEMA_VERSION,
            "title_taxonomy_only": True,
            "direction_classified": False,
            "predictive_weight_assigned": False,
            "forward_or_historical_outcome_read": False,
        }
        rows.append(
            {
                "event_id": event_id,
                "entity_id": str(item["entity_id"]),
                "domain_id": DOMAIN_ID,
                "event_family": EVENT_FAMILY[event_type],
                "event_type": event_type,
                "event_date": item["event_date"],
                "evidence_available_date": item["evidence_available_date"],
                "source_identity": CNINFO_SOURCE_ID,
                "provider": str(item["provider"]),
                "document_id": str(item["document_id"]),
                "revision_id": str(item["revision_id"]),
                "title": _clean_title(item["title"]),
                "source_url_identity": str(item["source_url_identity"]),
                "classification_basis": "ISSUER_TITLE_TAXONOMY_ONLY_NO_DIRECTION",
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(identity_payload),
                "availability_state": str(item["availability_state"]),
                "direction_classified": False,
                "predictive_weight_assigned": False,
                "outcome_read": False,
            }
        )
    return _validate_event_frame(pd.DataFrame(rows, columns=EVENT_COLUMNS))


def _validate_cde_url(value: object) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"www.cde.org.cn", "cde.org.cn", "www.nmpa.gov.cn", "nmpa.gov.cn"}:
        raise ValueError("CDE/NMPA snapshot source_url must remain on an official HTTPS host")
    return text


def normalize_cde_snapshot(
    snapshot: pd.DataFrame,
    *,
    trading_dates: Iterable[object],
) -> pd.DataFrame:
    """Normalize a manually captured official CDE/NMPA snapshot.

    Entity mapping must already be exact and auditable. This adapter never
    fuzzy-matches applicant names to listed issuers.
    """

    required = {
        "entity_id",
        "entity_mapping_basis",
        "category",
        "publication_date",
        "record_id",
        "applicant",
        "drug_name",
        "indication",
        "source_url",
    }
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"CDE/NMPA snapshot missing columns: {sorted(missing)}")
    calendar = _real_trading_calendar(trading_dates)
    rows: list[dict[str, object]] = []
    for _, item in snapshot.iterrows():
        category = str(item["category"]).strip()
        if category not in _CDE_CATEGORY_MAP:
            raise ValueError(f"unsupported frozen CDE category: {category}")
        mapping_basis = str(item["entity_mapping_basis"]).strip()
        if mapping_basis not in {"EXACT_APPLICANT_ALIAS_REGISTRY", "EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE"}:
            raise ValueError("CDE entity mapping must be exact and auditable")
        event_type = _CDE_CATEGORY_MAP[category]
        source_url = _validate_cde_url(item["source_url"])
        event_date = pd.Timestamp(item["publication_date"]).normalize()
        available_date, availability_rule = _market_available_date(
            str(event_date.date()), trading_dates=calendar
        )
        title = f"{category}:{str(item['drug_name']).strip()}:{str(item['indication']).strip()}"
        identity_payload = {
            "schema_version": SCHEMA_VERSION,
            "source_identity": CDE_SOURCE_ID,
            "entity_id": str(item["entity_id"]),
            "record_id": str(item["record_id"]),
            "event_type": event_type,
            "publication_date": str(event_date.date()),
        }
        provenance = {
            "source_identity": CDE_SOURCE_ID,
            "official_source_url": source_url,
            "category": category,
            "applicant": str(item["applicant"]).strip(),
            "drug_name": str(item["drug_name"]).strip(),
            "indication": str(item["indication"]).strip(),
            "entity_mapping_basis": mapping_basis,
            "availability_rule": availability_rule,
            "date_only_publication_delayed_to_next_real_trading_date": True,
            "direction_classified": False,
            "predictive_weight_assigned": False,
            "forward_or_historical_outcome_read": False,
        }
        rows.append(
            {
                "event_id": "idkpi:" + _stable_hash(identity_payload),
                "entity_id": str(item["entity_id"]),
                "domain_id": DOMAIN_ID,
                "event_family": EVENT_FAMILY[event_type],
                "event_type": event_type,
                "event_date": event_date,
                "evidence_available_date": available_date,
                "source_identity": CDE_SOURCE_ID,
                "provider": "NMPA_OR_CDE",
                "document_id": str(item["record_id"]),
                "revision_id": f"OFFICIAL_SNAPSHOT:{str(item['record_id'])}",
                "title": title,
                "source_url_identity": source_url,
                "classification_basis": "OFFICIAL_CDE_CATEGORY_NO_DIRECTION",
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(identity_payload),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "direction_classified": False,
                "predictive_weight_assigned": False,
                "outcome_read": False,
            }
        )
    return _validate_event_frame(pd.DataFrame(rows, columns=EVENT_COLUMNS))


def build_sector_kpi_raw_result(
    *,
    cninfo_events: pd.DataFrame,
    cde_events: pd.DataFrame | None = None,
    source_coverage: dict[str, object] | None = None,
) -> SectorKpiRawResult:
    pieces = [cninfo_events]
    if cde_events is not None:
        pieces.append(cde_events)
    events = _validate_event_frame(
        pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=EVENT_COLUMNS)
    )
    by_family = {
        str(k): int(v)
        for k, v in events["event_family"].value_counts().sort_index().items()
    } if not events.empty else {}
    by_type = {
        str(k): int(v)
        for k, v in events["event_type"].value_counts().sort_index().items()
    } if not events.empty else {}
    entities = sorted(set(events["entity_id"].astype(str))) if not events.empty else []
    latest = None
    earliest = None
    if not events.empty:
        latest = str(pd.to_datetime(events["evidence_available_date"]).max().date())
        earliest = str(pd.to_datetime(events["evidence_available_date"]).min().date())
    summary: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "domain_id": DOMAIN_ID,
        "state": "RAW_EVENT_CONTEXT_MATERIALIZED_NOT_FORMALLY_QUALIFIED",
        "entities": entities,
        "event_rows": int(len(events)),
        "events_by_family": by_family,
        "events_by_type": by_type,
        "earliest_evidence_available_date": earliest,
        "latest_evidence_available_date": latest,
        "source_coverage": source_coverage or {},
        "cde_nmpa_snapshot_materialized": bool(
            cde_events is not None and not cde_events.empty
        ),
        "sector_931152_point_in_time_aggregation_computed": False,
        "sector_kpi_state_classified": False,
        "supportive_or_adverse_direction_classified": False,
        "predictive_score_computed": False,
        "predictive_weights_assigned": False,
        "forward_prices_or_returns_used": False,
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "parameter_search_run": False,
        "threshold_search_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
    }
    return SectorKpiRawResult(events=events, summary=summary)


__all__ = [
    "BD_LICENSING",
    "CDE_SOURCE_ID",
    "CLINICAL_REGULATORY",
    "CNINFO_SOURCE_ID",
    "DOMAIN_ID",
    "EVENT_COLUMNS",
    "EVENT_FAMILY",
    "SCHEMA_VERSION",
    "SectorKpiRawResult",
    "build_sector_kpi_raw_result",
    "classify_innovation_drug_title",
    "normalize_cde_snapshot",
    "normalize_cninfo_sector_events",
]
