from __future__ import annotations

import json
from typing import Iterable

import pandas as pd

from .official_policy_archive import POLICY_ENTITY_ID, POLICY_SOURCE_ID


CNINFO_SOURCE_ID = "CNINFO_ANNOUNCEMENT_ARCHIVE"
SSE_SOURCE_ID = "SSE_ANNOUNCEMENT_ARCHIVE"
SZSE_SOURCE_ID = "SZSE_ANNOUNCEMENT_ARCHIVE"
NMPA_CDE_SOURCE_ID = "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE"

_EXPLICIT_NEGATIVE_TYPES = {
    "MAJOR_NEGATIVE_EVENT",
    "CREDIT_EVENT",
    "EARNINGS_WARNING",
    "GUIDANCE_DOWN",
    "REGULATORY_NEGATIVE",
}


def _entity_id(symbol: str, market: str) -> str:
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    suffix = str(market).strip().upper()
    if suffix not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"unsupported market for frozen scope: {market}")
    return f"{code}.{suffix}"


def _window_complete(
    coverage: pd.DataFrame,
    *,
    source_identity: str,
    entity_id: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> bool:
    required = {"source_identity", "entity_id", "coverage_start", "coverage_end", "query_status"}
    if coverage is None or coverage.empty or required - set(coverage.columns):
        return False
    x = coverage[
        coverage["source_identity"].astype(str).eq(source_identity)
        & coverage["entity_id"].astype(str).eq(entity_id)
    ].copy()
    if x.empty:
        return False
    x["coverage_start"] = pd.to_datetime(x["coverage_start"], errors="coerce").dt.normalize()
    x["coverage_end"] = pd.to_datetime(x["coverage_end"], errors="coerce").dt.normalize()
    # A source can have multiple mandatory coverage segments (for example the
    # fixed CSRC lists). Every present segment must prove the requested window.
    if "coverage_segment" in x.columns:
        for _, part in x.groupby("coverage_segment", dropna=False):
            ok = part[
                part["query_status"].astype(str).eq("COMPLETE_WINDOW")
                & part["coverage_start"].le(start)
                & part["coverage_end"].ge(end)
            ]
            if ok.empty:
                return False
        return bool(len(x))
    ok = x[
        x["query_status"].astype(str).eq("COMPLETE_WINDOW")
        & x["coverage_start"].le(start)
        & x["coverage_end"].ge(end)
    ]
    return not ok.empty


def build_major_negative_coverage_ledger(
    *,
    frozen_scope: pd.DataFrame,
    issuer_coverage: pd.DataFrame,
    policy_coverage: pd.DataFrame,
    start_date: object,
    end_date: object,
    nmpa_cde_coverage: pd.DataFrame | None = None,
    nmpa_cde_applicable_entities: Iterable[str] = (),
) -> pd.DataFrame:
    """Build source/entity applicability without requiring irrelevant sources.

    CNINFO plus the entity's own exchange archive are universal issuer sources.
    CSRC policy/regulatory coverage is market-wide and applies to every entity.
    NMPA/CDE is required only for entities explicitly supplied by an already
    frozen applicability contract; an empty applicability set does not create a
    new all-universe requirement.
    """

    required_scope = {"symbol", "market"}
    missing = required_scope - set(frozen_scope.columns)
    if missing:
        raise ValueError(f"frozen scope missing columns: {sorted(missing)}")
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    nmpa_entities = {str(value) for value in nmpa_cde_applicable_entities}

    rows: list[dict[str, object]] = []
    seen_entities: set[str] = set()
    for _, item in frozen_scope.iterrows():
        entity = _entity_id(str(item["symbol"]), str(item["market"]))
        if entity in seen_entities:
            continue
        seen_entities.add(entity)
        market = str(item["market"]).upper()
        exchange_source = SSE_SOURCE_ID if market == "SH" else SZSE_SOURCE_ID if market == "SZ" else None
        if exchange_source is None:
            # The currently frozen STAR50+ChiNext50 scope contains SH/SZ only.
            # A BJ member would fail closed until a registered issuer archive is
            # explicitly added; never infer one here.
            rows.append(
                {
                    "entity_id": entity,
                    "source_identity": "UNREGISTERED_EXCHANGE_ARCHIVE",
                    "applicable": True,
                    "coverage_complete": False,
                    "coverage_start": start,
                    "coverage_end": end,
                    "reason": "NO_REGISTERED_EXCHANGE_ARCHIVE_FOR_MARKET",
                }
            )
        else:
            for source in (CNINFO_SOURCE_ID, exchange_source):
                rows.append(
                    {
                        "entity_id": entity,
                        "source_identity": source,
                        "applicable": True,
                        "coverage_complete": _window_complete(
                            issuer_coverage,
                            source_identity=source,
                            entity_id=entity,
                            start=start,
                            end=end,
                        ),
                        "coverage_start": start,
                        "coverage_end": end,
                        "reason": "REGISTERED_ISSUER_SOURCE",
                    }
                )

        rows.append(
            {
                "entity_id": entity,
                "source_identity": POLICY_SOURCE_ID,
                "applicable": True,
                "coverage_complete": _window_complete(
                    policy_coverage,
                    source_identity=POLICY_SOURCE_ID,
                    entity_id=POLICY_ENTITY_ID,
                    start=start,
                    end=end,
                ),
                "coverage_start": start,
                "coverage_end": end,
                "reason": "MARKET_WIDE_POLICY_REGULATORY_SOURCE",
            }
        )
        if entity in nmpa_entities:
            rows.append(
                {
                    "entity_id": entity,
                    "source_identity": NMPA_CDE_SOURCE_ID,
                    "applicable": True,
                    "coverage_complete": _window_complete(
                        nmpa_cde_coverage if nmpa_cde_coverage is not None else pd.DataFrame(),
                        source_identity=NMPA_CDE_SOURCE_ID,
                        entity_id=entity,
                        start=start,
                        end=end,
                    ),
                    "coverage_start": start,
                    "coverage_end": end,
                    "reason": "EXPLICIT_FROZEN_NMPA_CDE_APPLICABILITY",
                }
            )
    return pd.DataFrame(rows)


def review_major_negative_events(
    *,
    coverage_ledger: pd.DataFrame,
    evidence_records: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    required = {"entity_id", "source_identity", "applicable", "coverage_complete"}
    missing = required - set(coverage_ledger.columns)
    if missing:
        raise ValueError(f"major-negative coverage ledger missing columns: {sorted(missing)}")
    entities = sorted(coverage_ledger["entity_id"].dropna().astype(str).unique())
    review_rows: list[dict[str, object]] = []
    for entity in entities:
        coverage = coverage_ledger[
            coverage_ledger["entity_id"].astype(str).eq(entity)
            & coverage_ledger["applicable"].astype(bool)
        ]
        complete = bool(len(coverage)) and bool(coverage["coverage_complete"].astype(bool).all())
        if evidence_records is None or evidence_records.empty:
            negative = pd.DataFrame()
        else:
            entity_records = evidence_records[
                evidence_records["entity_id"].astype(str).isin({entity, POLICY_ENTITY_ID})
            ]
            negative = entity_records[
                entity_records["evidence_type"].astype(str).isin(_EXPLICIT_NEGATIVE_TYPES)
            ]
        ids = (
            sorted(negative["evidence_id"].astype(str).tolist())
            if len(negative) and "evidence_id" in negative.columns
            else []
        )
        review_rows.append(
            {
                "entity_id": entity,
                "source_coverage_complete": complete,
                "review_complete": complete,
                "explicit_negative_event_count": int(len(negative)),
                "explicit_negative_evidence_ids": json.dumps(ids, ensure_ascii=False),
                "exclusion_clear": bool(complete and len(negative) == 0),
            }
        )
    review = pd.DataFrame(review_rows)
    all_reviewed = bool(len(review)) and bool(review["review_complete"].all())
    summary = {
        "source_coverage_complete": all_reviewed,
        "major_negative_event_exclusion_complete": all_reviewed,
        "major_negative_event_exclusion_semantics": "COVERAGE_AND_REVIEW_PROCESS_COMPLETE_NOT_ASSERTION_OF_NO_EVENTS",
        "entities_reviewed": int(len(review)),
        "entities_with_explicit_negative_events": int(
            (review["explicit_negative_event_count"] > 0).sum()
        )
        if len(review)
        else 0,
        "absence_of_captured_negative_does_not_imply_clear_without_complete_coverage": True,
    }
    return review, summary


__all__ = [
    "CNINFO_SOURCE_ID",
    "SSE_SOURCE_ID",
    "SZSE_SOURCE_ID",
    "NMPA_CDE_SOURCE_ID",
    "build_major_negative_coverage_ledger",
    "review_major_negative_events",
]
