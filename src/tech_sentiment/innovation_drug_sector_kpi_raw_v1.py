from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Iterable
from urllib.parse import urlparse
import time

import pandas as pd

from .bounded_retry import is_transient_network_error
from .pit_public_materialization import (
    REQUIRED_PIT_COLUMNS,
    _market_available_date,
    _real_trading_calendar,
    _stable_hash,
    normalize_cninfo_announcements,
    validate_materialized_pit_records,
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
    if "合作协议" in text and any(
        token in text
        for token in (
            "药品授权合作",
            "药品合作",
            "药物合作",
            "新药",
            "研发合作",
            "临床合作",
            "治疗合作",
            "商业化合作",
            "里程碑付款",
            "里程碑支付",
        )
    ):
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

SECTOR_QUERY_KEYWORDS = (
    "终止临床",
    "临床试验终止",
    "临床失败",
    "撤回药品注册申请",
    "突破性治疗",
    "上市许可申请",
    "药品注册批准",
    "获批上市",
    "批准上市",
    "临床试验批准",
    "许可协议",
    "授权协议",
    "独家许可",
    "许可及合作协议",
    "合作协议",
)

# Every title branch accepted by classify_innovation_drug_title() is covered by
# at least one market-wide query token. The query is only a transport narrowing
# step; the title classifier remains the semantic gate.
EVENT_TYPE_QUERY_COVERAGE = {
    "CLINICAL_TRIAL_TERMINATION": ("终止临床", "临床试验终止", "临床失败"),
    "REGISTRATION_APPLICATION_WITHDRAWAL": ("撤回药品注册申请",),
    "BREAKTHROUGH_THERAPY_DESIGNATION": ("突破性治疗",),
    "MARKETING_APPLICATION_ACCEPTED": ("上市许可申请",),
    "MARKETING_APPLICATION_PRIORITY_REVIEW": ("上市许可申请",),
    "MARKETING_APPROVAL": ("药品注册批准", "获批上市", "批准上市"),
    "CLINICAL_TRIAL_AUTHORIZATION": ("临床试验批准",),
    "BD_LICENSE_OR_COLLABORATION": ("许可协议", "授权协议", "独家许可", "许可及合作协议"),
    "BD_COLLABORATION": ("合作协议",),
}


@dataclass(frozen=True)
class SectorKeywordMaterializationResult:
    pit_records: pd.DataFrame
    query_coverage: pd.DataFrame
    query_errors: pd.DataFrame


def validate_931152_membership_scope(membership: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "effective_start",
        "effective_end",
        "source_index",
        "universe_mode",
        "source_scope",
        "source_identity",
        "source_member_file_sha256",
        "source_reference",
    }
    missing = required - set(membership.columns)
    if missing:
        raise ValueError(f"931152 KPI membership scope missing columns: {sorted(missing)}")
    frame = membership.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["effective_start"] = pd.to_datetime(frame["effective_start"], errors="raise").dt.normalize()
    frame["effective_end"] = pd.to_datetime(frame["effective_end"], errors="raise").dt.normalize()
    if (frame["effective_end"] < frame["effective_start"]).any():
        raise ValueError("931152 KPI membership contains reversed intervals")
    if set(frame["source_index"].astype(str).str.zfill(6)) != {"931152"}:
        raise ValueError("931152 KPI membership contains another source index")
    if set(frame["universe_mode"].astype(str)) != {"point_in_time"}:
        raise ValueError("931152 KPI membership must remain point_in_time")
    if frame["source_member_file_sha256"].astype(str).str.len().ne(64).any():
        raise ValueError("931152 KPI membership source file hashes must be SHA256")
    for symbol, part in frame.sort_values(["symbol", "effective_start", "effective_end"]).groupby("symbol"):
        previous_end = None
        for start, end in part[["effective_start", "effective_end"]].itertuples(index=False):
            if previous_end is not None and pd.Timestamp(start) <= pd.Timestamp(previous_end):
                raise ValueError(f"931152 KPI membership intervals overlap for {symbol}")
            previous_end = pd.Timestamp(end)
    return frame.sort_values(["effective_start", "symbol", "effective_end"]).reset_index(drop=True)


def materialize_cninfo_market_keyword_archive(
    *,
    keywords: Iterable[str],
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    candidate_symbols: Iterable[str],
    fetcher: Callable[..., pd.DataFrame] | None = None,
    fetch_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> SectorKeywordMaterializationResult:
    """Fetch a frozen market-wide keyword set and normalize exact issuer records.

    Query keywords narrow transport only. A record is not a domain event until it
    independently passes classify_innovation_drug_title(). Candidate-symbol
    filtering uses only the union of already-qualified PIT membership symbols;
    exact-date membership filtering happens in filter_events_to_pit_membership().
    """

    if fetcher is None:
        import akshare as ak  # type: ignore

        fetcher = ak.stock_zh_a_disclosure_report_cninfo
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    calendar = _real_trading_calendar(trading_dates)
    candidate = sorted({str(value).zfill(6) for value in candidate_symbols})
    if not candidate:
        raise ValueError("sector keyword materialization requires candidate symbols")
    candidate_set = set(candidate)
    query_terms = tuple(dict.fromkeys(str(value).strip() for value in keywords if str(value).strip()))
    if not query_terms:
        raise ValueError("sector keyword materialization requires frozen keywords")
    if fetch_attempts < 1:
        raise ValueError("fetch_attempts must be >= 1")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be >= 0")

    normalized_parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for keyword in query_terms:
        try:
            attempts_used = 0
            raw = None
            for attempt in range(fetch_attempts):
                attempts_used = attempt + 1
                try:
                    raw = fetcher(
                        symbol="",
                        market="沪深京",
                        keyword=keyword,
                        category="",
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                    )
                    break
                except Exception as exc:
                    retryable = isinstance(exc, json.JSONDecodeError) or is_transient_network_error(exc)
                    if not retryable or attempt + 1 >= fetch_attempts:
                        raise
                    delay = retry_backoff_seconds * (attempt + 1)
                    if delay:
                        time.sleep(delay)
            if not isinstance(raw, pd.DataFrame):
                raise ValueError("CNINFO market keyword fetcher must return a DataFrame")
            raw_rows = int(len(raw))
            required_raw = {"代码", "简称", "公告标题", "公告时间", "公告链接"}
            missing_raw = required_raw - set(raw.columns)
            if missing_raw:
                raise ValueError(
                    f"CNINFO market keyword response missing columns: {sorted(missing_raw)}"
                )
            raw = raw.drop_duplicates(
                subset=["代码", "公告链接"], keep="first"
            ).reset_index(drop=True)
            deduplicated_raw_rows = int(len(raw))
            if raw.empty:
                member_raw = raw.copy()
            else:
                member_raw = raw.loc[
                    raw["代码"].astype(str).str.zfill(6).isin(candidate_set)
                ].copy()
            member_rows = int(len(member_raw))
            normalized_rows = 0
            if member_rows:
                member_raw["_symbol"] = member_raw["代码"].astype(str).str.zfill(6)
                for symbol, part in member_raw.groupby("_symbol", sort=True):
                    part = part.drop(columns=["_symbol"])
                    normalized = normalize_cninfo_announcements(
                        part,
                        symbol=str(symbol),
                        query_start=start,
                        query_end=end,
                        trading_dates=calendar,
                    )
                    if len(normalized):
                        normalized_parts.append(normalized)
                        normalized_rows += int(len(normalized))
            coverage_rows.append(
                {
                    "keyword": keyword,
                    "query_status": "COMPLETE_WINDOW",
                    "raw_market_rows": raw_rows,
                    "deduplicated_market_rows": deduplicated_raw_rows,
                    "candidate_union_rows": member_rows,
                    "normalized_candidate_records": normalized_rows,
                    "fetch_attempts_used": attempts_used,
                    "start_date": str(start.date()),
                    "end_date": str(end.date()),
                }
            )
        except Exception as exc:
            errors.append({"keyword": keyword, "error": f"{type(exc).__name__}: {exc}"})
            coverage_rows.append(
                {
                    "keyword": keyword,
                    "query_status": "FAILED",
                    "raw_market_rows": 0,
                    "deduplicated_market_rows": 0,
                    "candidate_union_rows": 0,
                    "normalized_candidate_records": 0,
                    "fetch_attempts_used": fetch_attempts,
                    "start_date": str(start.date()),
                    "end_date": str(end.date()),
                }
            )

    if normalized_parts:
        records = pd.concat(normalized_parts, ignore_index=True, sort=False)
        records = records.drop_duplicates(subset=["evidence_id"], keep="first").reset_index(drop=True)
        records = validate_materialized_pit_records(records)
    else:
        records = pd.DataFrame(
            columns=list(REQUIRED_PIT_COLUMNS)
            + ["title", "source_url_identity", "captured_at_utc"]
        )
    coverage = pd.DataFrame(coverage_rows)
    error_frame = pd.DataFrame(errors, columns=["keyword", "error"])
    return SectorKeywordMaterializationResult(
        pit_records=records,
        query_coverage=coverage,
        query_errors=error_frame,
    )


def filter_events_to_pit_membership(
    events: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Keep events knowable while the exact issuer was an active 931152 member."""

    scope = validate_931152_membership_scope(membership)
    if events.empty:
        out = events.copy()
        for column in (
            "membership_effective_start",
            "membership_effective_end",
            "membership_source_scope",
            "membership_source_identity",
            "membership_source_reference",
            "pit_member_at_evidence_available_date",
        ):
            out[column] = pd.Series(dtype=object)
        return out

    required = set(EVENT_COLUMNS)
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"sector events missing columns: {sorted(missing)}")
    event_frame = _validate_event_frame(events)
    event_frame["_symbol"] = event_frame["entity_id"].astype(str).str.split(".").str[0].str.zfill(6)
    event_frame["_available"] = pd.to_datetime(
        event_frame["evidence_available_date"], errors="raise"
    ).dt.normalize()

    matches: list[dict[str, object]] = []
    grouped = {symbol: part for symbol, part in scope.groupby("symbol", sort=False)}
    for _, event in event_frame.iterrows():
        intervals = grouped.get(str(event["_symbol"]))
        if intervals is None:
            continue
        active = intervals.loc[
            intervals["effective_start"].le(event["_available"])
            & intervals["effective_end"].ge(event["_available"])
        ]
        if len(active) > 1:
            raise ValueError("multiple PIT membership intervals match one event")
        if active.empty:
            continue
        interval = active.iloc[0]
        payload = event.drop(labels=["_symbol", "_available"]).to_dict()
        payload.update(
            {
                "membership_effective_start": str(pd.Timestamp(interval["effective_start"]).date()),
                "membership_effective_end": str(pd.Timestamp(interval["effective_end"]).date()),
                "membership_source_scope": str(interval["source_scope"]),
                "membership_source_identity": str(interval["source_identity"]),
                "membership_source_reference": str(interval["source_reference"]),
                "pit_member_at_evidence_available_date": True,
            }
        )
        matches.append(payload)
    return pd.DataFrame(matches)


def build_931152_sector_raw_summary(
    *,
    events: pd.DataFrame,
    membership: pd.DataFrame,
    query_coverage: pd.DataFrame,
    query_errors: pd.DataFrame,
    start_date: object,
    end_date: object,
) -> dict[str, object]:
    scope = validate_931152_membership_scope(membership)
    required_queries = set(SECTOR_QUERY_KEYWORDS)
    observed_queries = set(query_coverage.get("keyword", pd.Series(dtype=str)).astype(str))
    complete_queries = set(
        query_coverage.loc[
            query_coverage.get("query_status", pd.Series(dtype=str)).astype(str).eq("COMPLETE_WINDOW"),
            "keyword",
        ].astype(str)
    ) if len(query_coverage) else set()
    if observed_queries != required_queries:
        raise ValueError("sector keyword query coverage does not match frozen query set")
    if complete_queries != required_queries or not query_errors.empty:
        raise ValueError("sector keyword query coverage is incomplete")

    if events.empty:
        event_rows = 0
        entities = []
        by_family = {}
        by_type = {}
        earliest = None
        latest = None
    else:
        if not events["pit_member_at_evidence_available_date"].astype(bool).all():
            raise ValueError("sector raw events must all pass PIT membership")
        if events["direction_classified"].astype(bool).any():
            raise ValueError("sector raw events cannot classify direction")
        if events["predictive_weight_assigned"].astype(bool).any():
            raise ValueError("sector raw events cannot assign predictive weights")
        if events["outcome_read"].astype(bool).any():
            raise ValueError("sector raw events cannot read outcomes")
        event_rows = int(len(events))
        entities = sorted(set(events["entity_id"].astype(str)))
        by_family = {
            str(k): int(v)
            for k, v in events["event_family"].value_counts().sort_index().items()
        }
        by_type = {
            str(k): int(v)
            for k, v in events["event_type"].value_counts().sort_index().items()
        }
        available = pd.to_datetime(events["evidence_available_date"], errors="raise").dt.normalize()
        earliest = str(available.min().date())
        latest = str(available.max().date())

    return {
        "schema_version": "innovation-drug-sector-kpi-931152-raw-v1",
        "domain_id": DOMAIN_ID,
        "index_code": "931152",
        "state": "PIT_MEMBER_FILTERED_RAW_EVENT_COVERAGE_MATERIALIZED_NOT_FORMALLY_QUALIFIED",
        "scope_start": str(pd.Timestamp(start_date).date()),
        "scope_end": str(pd.Timestamp(end_date).date()),
        "membership_scope_start": str(scope["effective_start"].min().date()),
        "membership_scope_end": str(scope["effective_end"].max().date()),
        "membership_interval_rows": int(len(scope)),
        "membership_unique_symbols": int(scope["symbol"].nunique()),
        "frozen_query_keywords": list(SECTOR_QUERY_KEYWORDS),
        "query_count": int(len(query_coverage)),
        "complete_query_count": int(query_coverage["query_status"].eq("COMPLETE_WINDOW").sum()),
        "query_error_rows": int(len(query_errors)),
        "event_rows": event_rows,
        "entities_with_events": entities,
        "entities_with_events_count": int(len(entities)),
        "events_by_family": by_family,
        "events_by_type": by_type,
        "earliest_evidence_available_date": earliest,
        "latest_evidence_available_date": latest,
        "event_identity_is_direction": False,
        "supportive_or_adverse_state_defined": False,
        "event_weight_defined": False,
        "clinical_stage_weight_defined": False,
        "bd_transaction_value_weight_defined": False,
        "sector_score_defined": False,
        "company_score_defined": False,
        "absence_of_event_may_imply_negative_or_positive_state": False,
        "cde_nmpa_snapshot_materialized": False,
        "cde_nmpa_state": "DATA_INSUFFICIENT_NOT_MATERIALIZED",
        "sector_kpi_formal_state": "DATA_INSUFFICIENT",
        "forward_prices_or_returns_used": False,
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "parameter_search_run": False,
        "threshold_search_run": False,
        "weight_search_run": False,
        "model_training_run": False,
        "evidence_qualification_changed": False,
        "production_changed": False,
        "trading_authority_changed": False,
        "current_constituent_backfill_used": False,
    }



__all__ = [
    "BD_LICENSING",
    "CDE_SOURCE_ID",
    "CLINICAL_REGULATORY",
    "CNINFO_SOURCE_ID",
    "DOMAIN_ID",
    "EVENT_COLUMNS",
    "EVENT_FAMILY",
    "SCHEMA_VERSION",
    "SECTOR_QUERY_KEYWORDS",
    "EVENT_TYPE_QUERY_COVERAGE",
    "SectorKeywordMaterializationResult",
    "SectorKpiRawResult",
    "build_931152_sector_raw_summary",
    "build_sector_kpi_raw_result",
    "filter_events_to_pit_membership",
    "materialize_cninfo_market_keyword_archive",
    "classify_innovation_drug_title",
    "normalize_cde_snapshot",
    "normalize_cninfo_sector_events",
    "validate_931152_membership_scope",
]
