from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import pandas as pd

from tech_sentiment.canonical_materialization import canonicalize_metadata
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.major_negative_review import (
    build_major_negative_coverage_ledger,
    review_major_negative_events,
)
from tech_sentiment.official_filing_facts import (
    DERIVED_FUNDAMENTAL_SOURCE_ID,
    FILING_FACT_COLUMNS,
    derive_fundamental_trend_evidence,
)
from tech_sentiment.pit_public_materialization import (
    _stable_hash,
    validate_materialized_pit_records,
)
from tech_sentiment.pit_replay_audit import audit_pit_replay
from tech_sentiment.trailing_valuation_pit import (
    VALUATION_SOURCE_ID,
    build_trailing_valuation_rail,
    valuation_rail_to_pit_evidence,
)
from tech_sentiment.v4a_stage_artifact import verify_stage_receipt


SCHEMA_VERSION = "capital-pit-derived-materialization-v4a"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _entity(symbol: object) -> str:
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


def _canonicalize_provenance(records: pd.DataFrame) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame() if records is None else records.copy()
    out = records.copy()
    values: list[str] = []
    for _, row in out.iterrows():
        payload = json.loads(str(row["provenance"]))
        if not isinstance(payload, dict):
            raise ValueError("derived PIT provenance must be a JSON object")
        payload.setdefault("source_identity", str(row["source_identity"]))
        payload.setdefault("provider", str(row["provider"]))
        values.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    out["provenance"] = values
    return validate_materialized_pit_records(out)


def _earnings_down_events(earnings_evidence: pd.DataFrame) -> pd.DataFrame:
    if earnings_evidence.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, row in earnings_evidence.iterrows():
        payload = json.loads(str(row["evidence_payload"]))
        if str(payload.get("earnings_expectation_direction") or "").upper() != "DOWN":
            continue
        negative_payload = {
            "major_event_type": "EARNINGS_WARNING",
            "major_event_direction": "NEGATIVE",
            "derived_from_evidence_id": str(row["evidence_id"]),
            "deterministic_mapping": "ISSUER_EARNINGS_DIRECTION_DOWN_TO_EARNINGS_WARNING",
        }
        provenance = json.loads(str(row["provenance"]))
        provenance["negative_event_mapping"] = negative_payload["deterministic_mapping"]
        identity = {
            "source_evidence_id": str(row["evidence_id"]),
            "document_id": str(row["document_id"]),
            "revision_id": str(row["revision_id"]),
        }
        rows.append(
            {
                "evidence_id": f"earnings-warning:{_stable_hash(identity)}",
                "entity_id": str(row["entity_id"]),
                "evidence_type": "EARNINGS_WARNING",
                "event_date": row["event_date"],
                "evidence_available_date": row["evidence_available_date"],
                "source_identity": str(row["source_identity"]),
                "provider": str(row["provider"]),
                "document_id": str(row["document_id"]),
                "revision_id": f"{row['revision_id']}:NEGATIVE_MAPPING",
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash({"identity": identity, "payload": negative_payload}),
                "availability_state": str(row["availability_state"]),
                "evidence_payload": json.dumps(negative_payload, ensure_ascii=False, sort_keys=True),
                "source_url_identity": row.get("source_url_identity", ""),
            }
        )
    return validate_materialized_pit_records(pd.DataFrame(rows)) if rows else pd.DataFrame()


def _valuation_readiness(
    rail: pd.DataFrame,
    *,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
) -> tuple[str, dict[str, object]]:
    if rail.empty:
        return "DATA_INSUFFICIENT", {
            "eligible_rows": 0,
            "usable_rows": 0,
            "row_level_data_insufficient_rows": 0,
            "coverage": 0.0,
        }
    target = rail[pd.to_datetime(rail["date"]).dt.normalize().between(target_start, target_end)]
    eligible = target[target["valuation_reference_date_20d"].notna()]
    usable = eligible[
        pd.to_numeric(eligible["trailing_pe"], errors="coerce").notna()
        & pd.to_numeric(eligible["trailing_pe_20d_reference"], errors="coerce").notna()
        & pd.to_numeric(eligible["valuation_change_20d"], errors="coerce").notna()
        & eligible["denominator_document_id"].notna()
    ]
    eligible_rows = int(len(eligible))
    usable_rows = int(len(usable))
    insufficient_rows = int(eligible_rows - usable_rows)
    coverage = usable_rows / eligible_rows if eligible_rows else 0.0
    state = (
        "QUALIFIED_INPUT"
        if eligible_rows and usable_rows > 0
        else "DATA_INSUFFICIENT"
    )
    return state, {
        "eligible_rows": eligible_rows,
        "usable_rows": usable_rows,
        "row_level_data_insufficient_rows": insufficient_rows,
        "coverage": coverage,
        "qualification_mode": (
            "SOURCE_PIPELINE_QUALIFIED_WITH_EXPLICIT_ROW_LEVEL_DATA_INSUFFICIENCY"
        ),
        "missing_valuation_rows_emit_no_evidence": True,
        "eligibility_rule": "TARGET_ROWS_WITH_EXACT_REAL_TRADING_CALENDAR_T_MINUS_20_REFERENCE_DATE",
        "missing_endpoint_fill": False,
    }


def _fundamental_readiness(
    evidence: pd.DataFrame,
    *,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
    expected_entities: set[str],
    filing_coverage: pd.DataFrame,
) -> tuple[str, dict[str, object]]:
    target = evidence.copy()
    if len(target):
        dates = pd.to_datetime(
            target["evidence_available_date"], errors="raise"
        ).dt.normalize()
        target = target[dates.between(target_start, target_end)].copy()
    allowed = {"HISTORICAL_RECONSTRUCTABLE", "DATA_INSUFFICIENT"}
    bad_states = (
        sorted(set(target["availability_state"].astype(str)) - allowed)
        if len(target)
        else []
    )
    if bad_states:
        raise ValueError(
            "fundamental evidence has invalid availability states: "
            + ",".join(bad_states)
        )
    qualified = target[
        target["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE")
    ] if len(target) else target
    insufficient = target[
        target["availability_state"].astype(str).eq("DATA_INSUFFICIENT")
    ] if len(target) else target
    accounted = (
        set(target["entity_id"].dropna().astype(str))
        if len(target)
        else set()
    )
    if {"entity_id", "query_status"}.issubset(filing_coverage.columns):
        soft = filing_coverage[
            filing_coverage["query_status"].astype(str).eq(
                "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
            )
        ]
        accounted.update(soft["entity_id"].dropna().astype(str))
    missing_accounted = sorted(expected_entities - accounted)
    state = (
        "QUALIFIED_INPUT"
        if not missing_accounted and len(qualified) > 0
        else "PARTIAL_COVERAGE" if len(qualified)
        else "DATA_INSUFFICIENT"
    )
    return state, {
        "target_records": int(len(target)),
        "qualified_records": int(len(qualified)),
        "data_insufficient_records": int(len(insufficient)),
        "qualified_entities": int(
            qualified["entity_id"].astype(str).nunique()
        ) if len(qualified) else 0,
        "accounted_entities": int(len(accounted)),
        "expected_entities": int(len(expected_entities)),
        "missing_accounted_entities": missing_accounted,
        "qualification_mode": (
            "SOURCE_PIPELINE_QUALIFIED_WITH_EXPLICIT_ROW_LEVEL_DATA_INSUFFICIENCY"
        ),
        "data_insufficient_rows_emit_no_fundamental_state": True,
    }


def _manifest_shard_identity(
    manifest: dict[str, object],
    *,
    kind: str,
    stage_dir: Path,
) -> tuple[int, int]:
    raw_count = manifest.get("shard_count")
    raw_index = manifest.get("shard_index")
    if raw_count is None or raw_index is None or isinstance(raw_count, bool) or isinstance(raw_index, bool):
        raise ValueError(f"{kind} invalid shard identity: {stage_dir}")
    try:
        count = int(raw_count)
        index = int(raw_index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{kind} invalid shard identity: {stage_dir}") from exc
    if count < 1 or index < 0 or index >= count:
        raise ValueError(f"{kind} invalid shard identity: {stage_dir}")
    return count, index


def _verified_shards(
    root: Path,
    *,
    kind: str,
    schema: str,
    source_commit: str,
    start_date: str,
    end_date: str,
    expected_symbols: set[str],
) -> list[Path]:
    receipts = sorted(root.rglob("receipt.json"))
    if not receipts:
        raise ValueError(f"no {kind} stage receipts found")
    stages: list[tuple[Path, dict[str, object]]] = []
    shard_counts: set[int] = set()
    shard_indexes: list[int] = []
    symbols_seen: set[str] = set()
    for receipt in receipts:
        stage_dir = receipt.parent
        verify_stage_receipt(
            root=stage_dir,
            receipt_path=receipt,
            source_commit=source_commit,
            stage_kind=kind,
            start_date=start_date,
            end_date=end_date,
        )
        manifest = _read_json(stage_dir / "stage_manifest.json")
        if manifest.get("schema_version") != schema:
            raise ValueError(f"{kind} stage schema mismatch: {stage_dir}")
        if str(manifest.get("source_commit") or "") != source_commit:
            raise ValueError(f"{kind} stage commit mismatch: {stage_dir}")
        if str(manifest.get("start_date") or "") != start_date:
            raise ValueError(f"{kind} stage start mismatch: {stage_dir}")
        if str(manifest.get("end_date") or "") != end_date:
            raise ValueError(f"{kind} stage end mismatch: {stage_dir}")
        count, index = _manifest_shard_identity(
            manifest,
            kind=kind,
            stage_dir=stage_dir,
        )
        shard_counts.add(count)
        shard_indexes.append(index)
        shard_symbols = {str(value).zfill(6) for value in manifest.get("symbols", [])}
        if symbols_seen.intersection(shard_symbols):
            raise ValueError(f"{kind} shard symbol overlap detected")
        symbols_seen.update(shard_symbols)
        stages.append((stage_dir, manifest))
    if len(shard_counts) != 1:
        raise ValueError(f"{kind} inconsistent shard counts")
    count = next(iter(shard_counts))
    if sorted(shard_indexes) != list(range(count)):
        raise ValueError(f"{kind} incomplete shard indexes")
    if symbols_seen != expected_symbols:
        raise ValueError(
            f"{kind} shard symbol union mismatch: "
            f"missing={sorted(expected_symbols - symbols_seen)[:10]} "
            f"extra={sorted(symbols_seen - expected_symbols)[:10]}"
        )
    return [item[0] for item in sorted(stages, key=lambda item: int(item[1]["shard_index"]))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble verified parallel V4-A derived PIT stages.")
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--issuer-dir", required=True)
    parser.add_argument("--fundamental-root", required=True)
    parser.add_argument("--price-root", required=True)
    parser.add_argument("--policy-dir", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--issuer-source-commit", default="")
    parser.add_argument("--fundamental-source-commit", default="")
    parser.add_argument("--price-source-commit", default="")
    parser.add_argument("--policy-source-commit", default="")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    issuer_source_commit = args.issuer_source_commit or args.source_commit
    fundamental_source_commit = args.fundamental_source_commit or args.source_commit
    price_source_commit = args.price_source_commit or args.source_commit
    policy_source_commit = args.policy_source_commit or args.source_commit

    target_start = pd.Timestamp(args.start_date).normalize()
    target_end = pd.Timestamp(args.end_date).normalize()
    scope = pd.read_csv(args.symbols_csv, dtype=str)
    if "symbol" not in scope.columns:
        raise ValueError("frozen scope missing symbol")
    expected_symbols = {str(value).zfill(6) for value in scope["symbol"].dropna().astype(str)}
    expected_entities = {_entity(value) for value in expected_symbols}
    calendar = pd.read_csv(args.calendar_csv)
    if "date" not in calendar.columns:
        raise ValueError("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()

    issuer_dir = Path(args.issuer_dir)
    verify_stage_receipt(
        root=issuer_dir,
        receipt_path=issuer_dir / "receipt.json",
        source_commit=issuer_source_commit,
        stage_kind="issuer_aggregate",
        start_date=args.start_date,
        end_date=args.end_date,
    )
    issuer_manifest = _read_json(issuer_dir / "pit_materialization_manifest.json")
    if str(issuer_manifest.get("source_commit") or "") != issuer_source_commit:
        raise ValueError("issuer aggregate commit mismatch")
    issuer_evidence = _read_csv(issuer_dir / "pit_evidence.csv")
    issuer_coverage = _read_csv(issuer_dir / "pit_source_coverage.csv")
    if len(issuer_evidence):
        issuer_evidence = validate_materialized_pit_records(issuer_evidence)

    fundamental_dirs = _verified_shards(
        Path(args.fundamental_root),
        kind="fundamental_earnings",
        schema="v4a-fundamental-earnings-shard-v1",
        source_commit=fundamental_source_commit,
        start_date=args.start_date,
        end_date=args.end_date,
        expected_symbols=expected_symbols,
    )
    price_dirs = _verified_shards(
        Path(args.price_root),
        kind="prices",
        schema="v4a-price-shard-v1",
        source_commit=price_source_commit,
        start_date=args.start_date,
        end_date=args.end_date,
        expected_symbols=expected_symbols,
    )
    policy_dir = Path(args.policy_dir)
    verify_stage_receipt(
        root=policy_dir,
        receipt_path=policy_dir / "receipt.json",
        source_commit=policy_source_commit,
        stage_kind="policy",
        start_date=args.start_date,
        end_date=args.end_date,
    )
    policy_manifest = _read_json(policy_dir / "stage_manifest.json")
    if policy_manifest.get("schema_version") != "v4a-policy-stage-v1":
        raise ValueError("policy stage schema mismatch")

    fact_parts: list[pd.DataFrame] = []
    filing_coverage_parts: list[pd.DataFrame] = []
    earnings_direction_parts: list[pd.DataFrame] = []
    earnings_evidence_parts: list[pd.DataFrame] = []
    earnings_coverage_parts: list[pd.DataFrame] = []
    earnings_unclassified_parts: list[pd.DataFrame] = []
    filing_error_parts: list[pd.DataFrame] = []
    earnings_error_parts: list[pd.DataFrame] = []
    for stage in fundamental_dirs:
        facts = _read_csv(stage / "versioned_filing_facts.csv")
        if len(facts):
            fact_parts.append(facts)
        filing_coverage_parts.append(_read_csv(stage / "filing_coverage.csv"))
        directions = _read_csv(stage / "earnings_direction.csv")
        if len(directions):
            earnings_direction_parts.append(directions)
        evidence = _read_csv(stage / "earnings_direction_evidence.csv")
        if len(evidence):
            earnings_evidence_parts.append(validate_materialized_pit_records(evidence))
        earnings_coverage_parts.append(_read_csv(stage / "earnings_direction_coverage.csv"))
        unclassified = _read_csv(stage / "earnings_direction_unclassified.csv")
        if len(unclassified):
            earnings_unclassified_parts.append(unclassified)
        ferr = _read_csv(stage / "filing_errors.csv")
        if len(ferr):
            filing_error_parts.append(ferr)
        eerr = _read_csv(stage / "earnings_direction_errors.csv")
        if len(eerr):
            earnings_error_parts.append(eerr)

    facts = (
        pd.concat(fact_parts, ignore_index=True, sort=False)
        if fact_parts
        else pd.DataFrame(columns=list(FILING_FACT_COLUMNS))
    )
    if len(facts):
        facts["period_end"] = pd.to_datetime(facts["period_end"], errors="raise").dt.normalize()
        facts["evidence_available_date"] = pd.to_datetime(
            facts["evidence_available_date"], errors="raise"
        ).dt.normalize()
        if facts.duplicated(["entity_id", "document_id", "revision_id", "fact_type"]).any():
            raise ValueError("aggregated filing facts contain duplicate identities")
    filing_coverage = pd.concat(filing_coverage_parts, ignore_index=True, sort=False)
    if filing_coverage.empty or filing_coverage.duplicated(["entity_id"]).any():
        raise ValueError("filing coverage must contain exactly one row per entity")
    if set(filing_coverage["entity_id"].astype(str)) != expected_entities:
        raise ValueError("filing coverage entity set mismatch")

    trends = _canonicalize_provenance(derive_fundamental_trend_evidence(facts))
    fundamental = materialize_fundamental_state_evidence(
        facts,
        target_start_date=target_start,
        target_end_date=target_end,
    )
    fundamental_evidence = _canonicalize_provenance(fundamental.evidence)
    fundamental_state, fundamental_coverage_summary = _fundamental_readiness(
        fundamental_evidence,
        target_start=target_start,
        target_end=target_end,
        expected_entities=expected_entities,
        filing_coverage=filing_coverage,
    )

    earnings_directions = (
        pd.concat(earnings_direction_parts, ignore_index=True, sort=False)
        if earnings_direction_parts else pd.DataFrame()
    )
    if len(earnings_directions) and earnings_directions.duplicated(["entity_id", "document_id"]).any():
        raise ValueError("aggregated earnings directions contain duplicate documents")
    earnings_evidence = (
        validate_materialized_pit_records(
            pd.concat(earnings_evidence_parts, ignore_index=True, sort=False)
        )
        if earnings_evidence_parts else pd.DataFrame()
    )
    earnings_unclassified = (
        pd.concat(earnings_unclassified_parts, ignore_index=True, sort=False)
        if earnings_unclassified_parts else pd.DataFrame()
    )
    earnings_coverage = pd.concat(earnings_coverage_parts, ignore_index=True, sort=False)
    if earnings_coverage.empty or earnings_coverage.duplicated(["entity_id"]).any():
        raise ValueError("earnings coverage must contain exactly one row per entity")
    if set(earnings_coverage["entity_id"].astype(str)) != expected_entities:
        raise ValueError("earnings coverage entity set mismatch")
    earnings_complete = bool(
        earnings_coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all()
    )
    earnings_state = "QUALIFIED_INPUT" if earnings_complete else (
        "PARTIAL_COVERAGE"
        if earnings_coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").any()
        else "DATA_INSUFFICIENT"
    )
    earnings_negative = _earnings_down_events(earnings_evidence)

    price_parts: list[pd.DataFrame] = []
    price_coverage_parts: list[pd.DataFrame] = []
    price_error_parts: list[pd.DataFrame] = []
    for stage in price_dirs:
        prices = _read_csv(stage / "pit_stock_prices.csv")
        if len(prices):
            price_parts.append(prices)
        price_coverage_parts.append(_read_csv(stage / "pit_stock_price_coverage.csv"))
        errors = _read_csv(stage / "pit_stock_price_errors.csv")
        if len(errors):
            price_error_parts.append(errors)
    prices = pd.concat(price_parts, ignore_index=True, sort=False) if price_parts else pd.DataFrame()
    if len(prices):
        prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
        if prices.duplicated(["symbol", "date"]).any():
            raise ValueError("aggregated PIT prices contain duplicate symbol/date rows")
        prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
    price_coverage = pd.concat(price_coverage_parts, ignore_index=True, sort=False)
    if price_coverage.empty or price_coverage.duplicated(["symbol", "coverage_start", "coverage_end"]).any():
        raise ValueError("price coverage contains duplicate or no chunks")
    if set(price_coverage["symbol"].astype(str).str.zfill(6)) != expected_symbols:
        raise ValueError("price coverage symbol set mismatch")
    price_complete = int(price_coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").sum())
    price_summary = {
        "source_identity": "PUBLIC_A_SHARE_DAILY_CLOSE",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "symbols": len(expected_symbols),
        "chunks": int(len(price_coverage)),
        "complete_chunks": price_complete,
        "readiness_state": (
            "QUALIFIED_INPUT"
            if price_complete == len(price_coverage)
            else "PARTIAL_COVERAGE" if price_complete
            else "DATA_INSUFFICIENT"
        ),
        "adjustment": "NONE_UNADJUSTED_CLOSE",
        "no_forward_fill": True,
        "parallel_shards": len(price_dirs),
    }

    valuation_rail = build_trailing_valuation_rail(
        filing_facts=facts,
        stock_prices=prices,
        trading_dates=trading_dates,
    )
    valuation_state, valuation_coverage = _valuation_readiness(
        valuation_rail,
        target_start=target_start,
        target_end=target_end,
    )
    valuation_evidence = valuation_rail_to_pit_evidence(valuation_rail)
    if len(valuation_evidence):
        valuation_evidence = valuation_evidence[
            pd.to_datetime(valuation_evidence["evidence_available_date"], errors="raise")
            .dt.normalize()
            .between(target_start, target_end)
        ].reset_index(drop=True)
        valuation_evidence = _canonicalize_provenance(valuation_evidence)

    policy_evidence = _read_csv(policy_dir / "official_policy_regulatory_notice_archive.csv")
    if len(policy_evidence):
        policy_evidence = _canonicalize_provenance(policy_evidence)
    policy_coverage = _read_csv(policy_dir / "official_policy_regulatory_coverage.csv")
    policy_errors = _read_csv(policy_dir / "official_policy_regulatory_errors.csv")
    policy_summary = policy_manifest.get("policy_materialization")
    if not isinstance(policy_summary, dict):
        raise ValueError("policy stage summary missing")

    combined_parts = [
        frame for frame in (
            issuer_evidence,
            trends,
            fundamental_evidence,
            earnings_evidence,
            earnings_negative,
            valuation_evidence,
            policy_evidence,
        )
        if frame is not None and len(frame)
    ]
    combined = (
        validate_materialized_pit_records(pd.concat(combined_parts, ignore_index=True, sort=False))
        if combined_parts else pd.DataFrame()
    )
    coverage_ledger = build_major_negative_coverage_ledger(
        frozen_scope=scope,
        issuer_coverage=issuer_coverage,
        policy_coverage=policy_coverage,
        start_date=target_start,
        end_date=target_end,
        nmpa_cde_coverage=None,
        nmpa_cde_applicable_entities=(),
    )
    major_negative_review, major_negative_summary = review_major_negative_events(
        coverage_ledger=coverage_ledger,
        evidence_records=combined,
    )
    audit = audit_pit_replay(combined)

    raw_source_states = issuer_manifest.get("source_states")
    source_states = dict(raw_source_states) if isinstance(raw_source_states, dict) else {}
    source_states[DERIVED_FUNDAMENTAL_SOURCE_ID] = fundamental_state
    source_states[VALUATION_SOURCE_ID] = valuation_state
    source_states["OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"] = str(
        policy_summary.get("readiness_state") or "DATA_INSUFFICIENT"
    )
    filing_complete_statuses = {
        "COMPLETE_WINDOW",
        "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY",
    }
    filing_complete = int(
        filing_coverage["query_status"].astype(str).isin(
            filing_complete_statuses
        ).sum()
    )
    filing_soft_insufficient = int(
        filing_coverage["query_status"].astype(str).eq(
            "COMPLETE_WINDOW_WITH_DATA_INSUFFICIENCY"
        ).sum()
    )
    filing_summary = {
        "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
        "target_start_date": args.start_date,
        "end_date": args.end_date,
        "symbols": len(expected_symbols),
        "complete_entities": filing_complete,
        "soft_data_insufficient_entities": filing_soft_insufficient,
        "filing_fact_rows": int(len(facts)),
        "derived_trend_records": int(len(trends)),
        "numerical_trend_materialization_state": (
            "QUALIFIED_INPUT"
            if filing_complete == len(expected_symbols) and len(trends)
            else "PARTIAL_COVERAGE" if len(trends)
            else "DATA_INSUFFICIENT"
        ),
        "fundamental_state_mapping_state": "FUNDAMENTAL_PIT_STATE_CONTRACT_V1_DEFINED_SEPARATELY",
        "revision_ordering": "EVIDENCE_AVAILABLE_DATE_THEN_OFFICIAL_PUBLICATION_TIMESTAMP_FAIL_ON_AMBIGUOUS_TIE",
        "fundamental_state_thresholds_invented": False,
        "future_prices_or_returns_used": False,
        "hindsight_backfill": False,
        "parallel_shards": len(fundamental_dirs),
    }
    earnings_summary = {
        "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        "query_start_date": str((target_start - pd.DateOffset(years=2)).date()),
        "end_date": args.end_date,
        "symbols": len(expected_symbols),
        "forecast_documents": int(
            pd.to_numeric(earnings_coverage.get("forecast_documents", 0), errors="coerce").fillna(0).sum()
        ),
        "direction_documents": int(
            pd.to_numeric(earnings_coverage.get("direction_documents", 0), errors="coerce").fillna(0).sum()
        ),
        "canonical_evidence_records": int(len(earnings_evidence)),
        "unclassified_documents": int(len(earnings_unclassified)),
        "source_window_and_direction_extraction_complete": earnings_complete,
        "readiness_state": earnings_state,
        "unknown_is_not_not_down": True,
        "unclassified_never_emits_direction_evidence": True,
        "row_level_data_insufficiency_allowed": True,
        "numeric_threshold_used": False,
        "price_or_return_used": False,
        "parallel_shards": len(fundamental_dirs),
    }

    summary_with_runtime = {
        "schema_version": SCHEMA_VERSION,
        "status": "PUBLIC_PIT_MATERIALIZATION_COMPLETED",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source_commit": args.source_commit,
        "input_stage_source_commits": {
            "issuer": issuer_source_commit,
            "fundamental": fundamental_source_commit,
            "prices": price_source_commit,
            "policy": policy_source_commit,
        },
        "symbols": len(expected_symbols),
        "source_states": source_states,
        "earnings_direction_readiness_state": earnings_state,
        "major_negative_event_exclusion_complete": bool(
            major_negative_summary.get("major_negative_event_exclusion_complete")
        ),
        "major_negative_summary": major_negative_summary,
        "fundamental_state_contract": fundamental.summary,
        "fundamental_coverage": fundamental_coverage_summary,
        "filing_materialization": filing_summary,
        "earnings_materialization": earnings_summary,
        "price_materialization": price_summary,
        "valuation_coverage": valuation_coverage,
        "policy_materialization": policy_summary,
        "pit_audit": audit,
        "parallel_stage_receipts_verified": True,
        "nmpa_cde_applicability": "NO_UNIVERSAL_REQUIREMENT; EMPTY_UNTIL_FROZEN_ENTITY_APPLICABILITY_EXISTS",
        "future_prices_or_returns_used_for_fundamental_or_earnings": False,
        "predictive_research_run": False,
        "parameter_search_run": False,
        "holdout_run": False,
        "production_run": False,
    }
    summary = canonicalize_metadata(summary_with_runtime)
    if not isinstance(summary, dict):
        raise ValueError("derived PIT summary must be a mapping")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    issuer_files = [
        "pit_evidence.csv",
        "pit_source_coverage.csv",
        "pit_materialization_errors.csv",
        "pit_materialization_manifest.json",
        "cninfo_announcement_archive_evidence.csv",
        "cninfo_announcement_archive_coverage.csv",
        "cninfo_announcement_archive_errors.csv",
        "sse_announcement_archive_evidence.csv",
        "sse_announcement_archive_coverage.csv",
        "sse_announcement_archive_errors.csv",
        "szse_announcement_archive_evidence.csv",
        "szse_announcement_archive_coverage.csv",
        "szse_announcement_archive_errors.csv",
    ]
    for name in issuer_files:
        shutil.copy2(issuer_dir / name, out / name)

    facts.to_csv(out / "versioned_filing_facts.csv", index=False)
    trends.to_csv(out / "derived_pit_fundamental_trends.csv", index=False)
    fundamental_evidence.to_csv(out / "fundamental_state_evidence.csv", index=False)
    fundamental.coverage.to_csv(out / "fundamental_state_coverage.csv", index=False)
    earnings_directions.to_csv(out / "earnings_direction.csv", index=False)
    earnings_evidence.to_csv(out / "earnings_direction_evidence.csv", index=False)
    earnings_coverage.to_csv(out / "earnings_direction_coverage.csv", index=False)
    earnings_unclassified.to_csv(
        out / "earnings_direction_unclassified.csv", index=False
    )
    prices.to_csv(out / "pit_stock_prices.csv", index=False)
    price_coverage.to_csv(out / "pit_stock_price_coverage.csv", index=False)
    (
        pd.concat(price_error_parts, ignore_index=True, sort=False)
        if price_error_parts else pd.DataFrame(columns=["symbol", "chunk_start", "chunk_end", "error"])
    ).to_csv(out / "pit_stock_price_errors.csv", index=False)
    valuation_rail.to_csv(out / "trailing_valuation_rail.csv", index=False)
    valuation_evidence.to_csv(out / "derived_pit_trailing_valuation.csv", index=False)
    policy_evidence.to_csv(out / "official_policy_regulatory_notice_archive.csv", index=False)
    policy_coverage.to_csv(out / "official_policy_regulatory_coverage.csv", index=False)
    policy_errors.to_csv(out / "official_policy_regulatory_errors.csv", index=False)
    coverage_ledger.to_csv(out / "major_negative_coverage_ledger.csv", index=False)
    major_negative_review.to_csv(out / "major_negative_review.csv", index=False)
    combined.to_csv(out / "pit_evidence_extended.csv", index=False)
    _write_json(out / "derived_pit_materialization_manifest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
