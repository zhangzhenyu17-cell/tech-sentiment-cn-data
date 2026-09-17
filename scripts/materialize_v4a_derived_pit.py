from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.earnings_materialization import materialize_cninfo_earnings_directions
from tech_sentiment.filing_materialization import materialize_versioned_filing_facts
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.major_negative_review import (
    build_major_negative_coverage_ledger,
    review_major_negative_events,
)
from tech_sentiment.official_policy_archive import materialize_csrc_policy_archive
from tech_sentiment.pit_price_materialization import materialize_pit_stock_prices
from tech_sentiment.pit_public_materialization import _stable_hash, validate_materialized_pit_records
from tech_sentiment.pit_replay_audit import audit_pit_replay
from tech_sentiment.trailing_valuation_pit import (
    VALUATION_PROVIDER,
    VALUATION_SOURCE_ID,
    build_trailing_valuation_rail,
    valuation_rail_to_pit_evidence,
)


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


def _symbols(scope: pd.DataFrame) -> list[str]:
    if "symbol" not in scope.columns:
        raise ValueError("frozen scope missing symbol")
    return sorted({str(value).zfill(6) for value in scope["symbol"].dropna().astype(str)})


def _canonicalize_provenance(records: pd.DataFrame) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame() if records is None else records.copy()
    out = records.copy()
    values: list[str] = []
    for _, row in out.iterrows():
        raw = row.get("provenance")
        try:
            payload = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("derived PIT record contains invalid provenance JSON") from exc
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
                "ingestion_identity": _stable_hash(
                    {"identity": identity, "payload": negative_payload}
                ),
                "availability_state": str(row["availability_state"]),
                "evidence_payload": json.dumps(
                    negative_payload, ensure_ascii=False, sort_keys=True
                ),
                "source_url_identity": row.get("source_url_identity", ""),
            }
        )
    if not rows:
        return pd.DataFrame()
    return validate_materialized_pit_records(pd.DataFrame(rows))


def _valuation_readiness(
    rail: pd.DataFrame,
    *,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
) -> tuple[str, dict[str, object]]:
    if rail.empty:
        return "DATA_INSUFFICIENT", {"eligible_rows": 0, "usable_rows": 0, "coverage": 0.0}
    target = rail[
        pd.to_datetime(rail["date"]).dt.normalize().between(target_start, target_end)
    ].copy()
    target = target.sort_values(["entity_id", "date"])
    target["entity_row"] = target.groupby("entity_id").cumcount()
    eligible = target[target["entity_row"].ge(20)].copy()
    usable = eligible[
        pd.to_numeric(eligible["trailing_pe"], errors="coerce").notna()
        & pd.to_numeric(eligible["valuation_change_20d"], errors="coerce").notna()
        & eligible["denominator_document_id"].notna()
    ]
    eligible_rows = int(len(eligible))
    usable_rows = int(len(usable))
    coverage = usable_rows / eligible_rows if eligible_rows else 0.0
    if eligible_rows and usable_rows == eligible_rows:
        state = "QUALIFIED_INPUT"
    elif usable_rows:
        state = "PARTIAL_COVERAGE"
    else:
        state = "DATA_INSUFFICIENT"
    return state, {
        "eligible_rows": eligible_rows,
        "usable_rows": usable_rows,
        "coverage": coverage,
        "eligibility_rule": "PER_ENTITY_TARGET_WINDOW_ROWS_AFTER_FIRST_20_OBSERVATIONS",
    }


def _fundamental_readiness(
    evidence: pd.DataFrame,
    *,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
    expected_entities: int,
) -> tuple[str, dict[str, object]]:
    if evidence.empty:
        return "DATA_INSUFFICIENT", {"target_records": 0, "qualified_records": 0}
    x = evidence.copy()
    dates = pd.to_datetime(x["evidence_available_date"]).dt.normalize()
    target = x[dates.between(target_start, target_end)]
    qualified = target[
        target["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE")
    ]
    entities = int(qualified["entity_id"].astype(str).nunique()) if len(qualified) else 0
    all_target_qualified = bool(len(target)) and len(qualified) == len(target)
    if all_target_qualified and entities == expected_entities:
        state = "QUALIFIED_INPUT"
    elif len(qualified):
        state = "PARTIAL_COVERAGE"
    else:
        state = "DATA_INSUFFICIENT"
    return state, {
        "target_records": int(len(target)),
        "qualified_records": int(len(qualified)),
        "qualified_entities": entities,
        "expected_entities": expected_entities,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize V4-A derived PIT rails after issuer archives.")
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--issuer-evidence-csv", required=True)
    parser.add_argument("--issuer-coverage-csv", required=True)
    parser.add_argument("--issuer-manifest", required=True)
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", default=".cache/capital_pit_v4a/derived")
    parser.add_argument("--out-dir", default="output/pit_evidence_materialization")
    args = parser.parse_args()

    target_start = pd.Timestamp(args.start_date).normalize()
    target_end = pd.Timestamp(args.end_date).normalize()
    if target_end < target_start:
        raise SystemExit("end-date must not precede start-date")

    scope = _read_csv(Path(args.symbols_csv))
    symbols = _symbols(scope)
    calendar = _read_csv(Path(args.calendar_csv))
    if "date" not in calendar.columns:
        raise SystemExit("calendar CSV missing date")
    trading_dates = pd.to_datetime(calendar["date"], errors="raise").dt.normalize()
    issuer_evidence = _read_csv(Path(args.issuer_evidence_csv))
    issuer_coverage = _read_csv(Path(args.issuer_coverage_csv))
    issuer_manifest = _read_json(Path(args.issuer_manifest))

    checkpoint_root = Path(args.checkpoint_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    filings = materialize_versioned_filing_facts(
        symbols,
        target_start_date=target_start,
        end_date=target_end,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=checkpoint_root / "filings",
        warmup_years=2,
    )
    fundamental = materialize_fundamental_state_evidence(
        filings.facts,
        target_start_date=target_start,
        target_end_date=target_end,
    )
    fundamental_state, fundamental_coverage_summary = _fundamental_readiness(
        fundamental.evidence,
        target_start=target_start,
        target_end=target_end,
        expected_entities=len(symbols),
    )

    query_warmup_start = target_start - pd.DateOffset(years=2)
    earnings = materialize_cninfo_earnings_directions(
        symbols,
        query_start_date=query_warmup_start,
        end_date=target_end,
        trading_dates=trading_dates,
        source_commit=args.source_commit,
        checkpoint_dir=checkpoint_root / "filings",
    )
    earnings_evidence = _canonicalize_provenance(earnings.evidence)
    earnings_negative = _earnings_down_events(earnings_evidence)

    prices = materialize_pit_stock_prices(
        symbols,
        start_date=query_warmup_start,
        end_date=target_end,
        source_commit=args.source_commit,
        checkpoint_dir=checkpoint_root / "prices",
    )
    valuation_rail = build_trailing_valuation_rail(
        filing_facts=filings.facts,
        stock_prices=prices.prices,
    )
    valuation_state, valuation_coverage = _valuation_readiness(
        valuation_rail,
        target_start=target_start,
        target_end=target_end,
    )
    valuation_evidence = valuation_rail_to_pit_evidence(valuation_rail)
    if len(valuation_evidence):
        valuation_evidence = valuation_evidence[
            pd.to_datetime(valuation_evidence["evidence_available_date"])
            .dt.normalize()
            .between(target_start, target_end)
        ].reset_index(drop=True)
        valuation_evidence = _canonicalize_provenance(valuation_evidence)

    policy = materialize_csrc_policy_archive(
        start_date=target_start,
        end_date=target_end,
        trading_dates=trading_dates,
    )
    policy_evidence = _canonicalize_provenance(policy.records)
    trend_evidence = _canonicalize_provenance(filings.trends)
    fundamental_evidence = _canonicalize_provenance(fundamental.evidence)

    parts = [
        frame
        for frame in (
            issuer_evidence,
            trend_evidence,
            fundamental_evidence,
            earnings_evidence,
            earnings_negative,
            valuation_evidence,
            policy_evidence,
        )
        if frame is not None and len(frame)
    ]
    combined = (
        validate_materialized_pit_records(pd.concat(parts, ignore_index=True, sort=False))
        if parts
        else pd.DataFrame()
    )

    coverage_ledger = build_major_negative_coverage_ledger(
        frozen_scope=scope,
        issuer_coverage=issuer_coverage,
        policy_coverage=policy.coverage,
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
    source_states["DERIVED_PIT_FUNDAMENTAL_TRENDS"] = fundamental_state
    source_states[VALUATION_SOURCE_ID] = valuation_state
    source_states["OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE"] = str(
        policy.summary.get("readiness_state") or "DATA_INSUFFICIENT"
    )

    earnings_state = str(earnings.summary.get("readiness_state") or "DATA_INSUFFICIENT")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "PUBLIC_PIT_MATERIALIZATION_COMPLETED",
        "start_date": str(target_start.date()),
        "end_date": str(target_end.date()),
        "source_commit": args.source_commit,
        "symbols": len(symbols),
        "source_states": source_states,
        "earnings_direction_readiness_state": earnings_state,
        "major_negative_event_exclusion_complete": bool(
            major_negative_summary.get("major_negative_event_exclusion_complete")
        ),
        "major_negative_summary": major_negative_summary,
        "fundamental_state_contract": fundamental.summary,
        "fundamental_coverage": fundamental_coverage_summary,
        "filing_materialization": filings.summary,
        "earnings_materialization": earnings.summary,
        "price_materialization": prices.summary,
        "valuation_coverage": valuation_coverage,
        "policy_materialization": policy.summary,
        "pit_audit": audit,
        "nmpa_cde_applicability": "NO_UNIVERSAL_REQUIREMENT; EMPTY_UNTIL_FROZEN_ENTITY_APPLICABILITY_EXISTS",
        "future_prices_or_returns_used_for_fundamental_or_earnings": False,
        "predictive_research_run": False,
        "parameter_search_run": False,
        "holdout_run": False,
        "production_run": False,
    }

    filings.facts.to_csv(out / "versioned_filing_facts.csv", index=False)
    trend_evidence.to_csv(out / "derived_pit_fundamental_trends.csv", index=False)
    fundamental_evidence.to_csv(out / "fundamental_state_evidence.csv", index=False)
    fundamental.coverage.to_csv(out / "fundamental_state_coverage.csv", index=False)
    earnings.directions.to_csv(out / "earnings_direction.csv", index=False)
    earnings_evidence.to_csv(out / "earnings_direction_evidence.csv", index=False)
    earnings.coverage.to_csv(out / "earnings_direction_coverage.csv", index=False)
    prices.prices.to_csv(out / "pit_stock_prices.csv", index=False)
    prices.coverage.to_csv(out / "pit_stock_price_coverage.csv", index=False)
    prices.errors.to_csv(out / "pit_stock_price_errors.csv", index=False)
    valuation_rail.to_csv(out / "trailing_valuation_rail.csv", index=False)
    valuation_evidence.to_csv(out / "derived_pit_trailing_valuation.csv", index=False)
    policy_evidence.to_csv(out / "official_policy_regulatory_notice_archive.csv", index=False)
    policy.coverage.to_csv(out / "official_policy_regulatory_coverage.csv", index=False)
    policy.errors.to_csv(out / "official_policy_regulatory_errors.csv", index=False)
    coverage_ledger.to_csv(out / "major_negative_coverage_ledger.csv", index=False)
    major_negative_review.to_csv(out / "major_negative_review.csv", index=False)
    combined.to_csv(out / "pit_evidence_extended.csv", index=False)
    _write_json(out / "derived_pit_materialization_manifest.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
