from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.canonical_materialization import canonicalize_metadata
from tech_sentiment.official_pit_archives import SSE_SOURCE_ID, SZSE_SOURCE_ID
from tech_sentiment.pit_public_materialization import (
    CNINFO_SOURCE_ID,
    REQUIRED_PIT_COLUMNS,
    validate_materialized_pit_records,
)
from tech_sentiment.pit_replay_audit import audit_pit_replay
from tech_sentiment.v4a_stage_artifact import verify_stage_receipt


SCHEMA_VERSION = "capital-pit-public-issuer-ledger-v4a2"


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _entity(symbol: str) -> str:
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


def _expected_entities(scope: pd.DataFrame, source: str) -> set[str]:
    if "symbol" not in scope.columns:
        raise ValueError("scope missing symbol column")
    entities = {_entity(value) for value in scope["symbol"].dropna().astype(str)}
    if source == CNINFO_SOURCE_ID:
        return entities
    if source == SSE_SOURCE_ID:
        return {value for value in entities if value.endswith(".SH")}
    if source == SZSE_SOURCE_ID:
        return {value for value in entities if value.endswith(".SZ")}
    raise ValueError(f"unsupported issuer source: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate and verify parallel V4-A issuer PIT shards.")
    parser.add_argument("--shard-root", required=True)
    parser.add_argument("--symbols-csv", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--cninfo-source-commit", default="")
    parser.add_argument("--sse-source-commit", default="")
    parser.add_argument("--szse-source-commit", default="")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    root = Path(args.shard_root)
    scope = pd.read_csv(args.symbols_csv, dtype=str)
    expected_sources = {CNINFO_SOURCE_ID, SSE_SOURCE_ID, SZSE_SOURCE_ID}
    source_commits = {
        CNINFO_SOURCE_ID: args.cninfo_source_commit or args.source_commit,
        SSE_SOURCE_ID: args.sse_source_commit or args.source_commit,
        SZSE_SOURCE_ID: args.szse_source_commit or args.source_commit,
    }
    receipts = sorted(root.rglob("receipt.json"))
    if not receipts:
        raise SystemExit("no issuer shard receipts found")

    shards: list[tuple[Path, dict[str, object]]] = []
    seen_stage_ids: set[str] = set()
    for receipt in receipts:
        stage_dir = receipt.parent
        manifest = _read_json(stage_dir / "pit_materialization_manifest.json")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"issuer shard schema mismatch: {stage_dir}")
        selected = manifest.get("selected_sources")
        if not isinstance(selected, list) or len(selected) != 1:
            raise ValueError(f"issuer shard must contain exactly one source: {stage_dir}")
        source = str(selected[0])
        if source not in expected_sources:
            raise ValueError(f"unexpected issuer source: {source}")
        expected_source_commit = str(source_commits[source])
        payload = verify_stage_receipt(
            root=stage_dir,
            receipt_path=receipt,
            source_commit=expected_source_commit,
            stage_kind="issuer",
            start_date=args.start_date,
            end_date=args.end_date,
        )
        stage_id = str(payload.get("stage_id") or "")
        if stage_id in seen_stage_ids:
            raise ValueError(f"duplicate issuer stage id: {stage_id}")
        seen_stage_ids.add(stage_id)
        if str(manifest.get("source_commit") or "") != expected_source_commit:
            raise ValueError(f"issuer shard commit mismatch: {stage_id}")
        if str(manifest.get("start_date") or "") != str(args.start_date):
            raise ValueError(f"issuer shard start mismatch: {stage_id}")
        if str(manifest.get("end_date") or "") != str(args.end_date):
            raise ValueError(f"issuer shard end mismatch: {stage_id}")
        shards.append((stage_dir, manifest))

    by_source: dict[str, list[tuple[Path, dict[str, object]]]] = {
        source: [] for source in expected_sources
    }
    for item in shards:
        by_source[str(item[1]["selected_sources"][0])].append(item)
    missing_sources = sorted(source for source, rows in by_source.items() if not rows)
    if missing_sources:
        raise ValueError(f"missing issuer source shards: {missing_sources}")

    record_parts: list[pd.DataFrame] = []
    coverage_parts: list[pd.DataFrame] = []
    error_parts: list[pd.DataFrame] = []
    source_summaries: dict[str, dict[str, object]] = {}
    source_states: dict[str, str] = {}

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for source in sorted(expected_sources):
        rows = by_source[source]
        shard_counts = {int(item[1].get("shard_count") or 0) for item in rows}
        if len(shard_counts) != 1:
            raise ValueError(f"inconsistent shard_count for {source}")
        shard_count = next(iter(shard_counts))
        shard_indexes = sorted(int(item[1].get("shard_index") or 0) for item in rows)
        if shard_count < 1 or shard_indexes != list(range(shard_count)):
            raise ValueError(
                f"incomplete issuer shards for {source}: "
                f"indexes={shard_indexes} count={shard_count}"
            )

        source_records: list[pd.DataFrame] = []
        source_coverage: list[pd.DataFrame] = []
        source_errors: list[pd.DataFrame] = []
        tail_omitted = 0
        calendar_ids: set[str] = set()
        for stage_dir, manifest in rows:
            records = _read_csv(stage_dir / "pit_evidence.csv")
            coverage = _read_csv(stage_dir / "pit_source_coverage.csv")
            errors = _read_csv(stage_dir / "pit_materialization_errors.csv")
            if len(records):
                records = validate_materialized_pit_records(records)
                source_records.append(records)
            if len(coverage):
                source_coverage.append(coverage)
            if len(errors):
                source_errors.append(errors)
            calendar_ids.add(str(manifest.get("calendar_identity") or ""))
            summaries = manifest.get("source_summaries")
            if isinstance(summaries, dict) and isinstance(summaries.get(source), dict):
                tail_omitted += int(summaries[source].get("tail_records_beyond_asof_not_materialized") or 0)
        if len(calendar_ids) != 1 or "" in calendar_ids:
            raise ValueError(f"issuer shard calendar identity mismatch for {source}")

        records = (
            validate_materialized_pit_records(pd.concat(source_records, ignore_index=True, sort=False))
            if source_records
            else pd.DataFrame(columns=list(REQUIRED_PIT_COLUMNS))
        )
        coverage = (
            pd.concat(source_coverage, ignore_index=True, sort=False)
            if source_coverage
            else pd.DataFrame()
        )
        errors = (
            pd.concat(source_errors, ignore_index=True, sort=False)
            if source_errors
            else pd.DataFrame(columns=["source_identity", "entity_id", "error"])
        )
        if coverage.empty or "entity_id" not in coverage.columns or "source_identity" not in coverage.columns:
            raise ValueError(f"issuer coverage missing identity columns for {source}")
        if not coverage["source_identity"].astype(str).eq(source).all():
            raise ValueError(f"issuer coverage source mismatch for {source}")
        if coverage.duplicated(["source_identity", "entity_id"]).any():
            raise ValueError(f"duplicate issuer entity coverage rows for {source}")

        expected = _expected_entities(scope, source)
        actual = set(coverage["entity_id"].dropna().astype(str))
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"issuer entity coverage mismatch for {source}: missing={missing[:10]} extra={extra[:10]}"
            )
        complete = int(coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").sum())
        failed = int(coverage["query_status"].astype(str).eq("FAILED").sum())
        state = (
            "QUALIFIED_INPUT"
            if len(expected) and complete == len(expected) and failed == 0
            else "PARTIAL_COVERAGE" if complete
            else "DATA_INSUFFICIENT"
        )
        source_states[source] = state
        source_summaries[source] = {
            "source_identity": source,
            "start_date": str(args.start_date),
            "end_date": str(args.end_date),
            "source_commit": str(source_commits[source]),
            "aggregate_source_commit": str(args.source_commit),
            "calendar_identity": next(iter(calendar_ids)),
            "symbols": len(expected),
            "complete_symbol_queries": complete,
            "failed_symbol_queries": failed,
            "materialized_records": int(len(records)),
            "tail_records_beyond_asof_not_materialized": int(tail_omitted),
            "checkpoint_schema": "issuer-pit-exact-identity-v3-success-only",
            "readiness_state": state,
            "parallel_shards": shard_count,
        }

        slug = source.lower()
        records.to_csv(out / f"{slug}_evidence.csv", index=False)
        coverage.to_csv(out / f"{slug}_coverage.csv", index=False)
        errors.to_csv(out / f"{slug}_errors.csv", index=False)
        if len(records):
            record_parts.append(records)
        coverage_parts.append(coverage)
        if len(errors):
            error_parts.append(errors)

    combined_records = (
        validate_materialized_pit_records(pd.concat(record_parts, ignore_index=True, sort=False))
        if record_parts
        else pd.DataFrame(columns=list(REQUIRED_PIT_COLUMNS))
    )
    combined_coverage = pd.concat(coverage_parts, ignore_index=True, sort=False)
    combined_errors = (
        pd.concat(error_parts, ignore_index=True, sort=False)
        if error_parts
        else pd.DataFrame(columns=["source_identity", "entity_id", "error"])
    )
    audit = audit_pit_replay(combined_records) if len(combined_records) else {
        "records": 0,
        "required_fields_complete": False,
        "no_future_evidence": False,
        "duplicate_identity_free": False,
        "provenance_complete": False,
        "prefix_replay_filter_equality": False,
        "as_of_replay_equality": False,
        "revision_identity_complete": False,
        "later_revision_does_not_rewrite_prior_rows": False,
    }
    evidence_counts = (
        combined_records["evidence_type"].astype(str).value_counts().sort_index().to_dict()
        if len(combined_records)
        else {}
    )
    entities = {_entity(value) for value in scope["symbol"].dropna().astype(str)}
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "PUBLIC_ISSUER_PIT_MATERIALIZATION_COMPLETED",
        "start_date": str(args.start_date),
        "end_date": str(args.end_date),
        "source_commit": str(args.source_commit),
        "calendar_identity": source_summaries[CNINFO_SOURCE_ID]["calendar_identity"],
        "symbols": len(entities),
        "sh_symbols": len([value for value in entities if value.endswith(".SH")]),
        "sz_symbols": len([value for value in entities if value.endswith(".SZ")]),
        "materialized_records": int(len(combined_records)),
        "failed_symbol_queries": int(
            sum(int(item["failed_symbol_queries"]) for item in source_summaries.values())
        ),
        "source_states": source_states,
        "source_summaries": source_summaries,
        "input_source_commits": dict(sorted(source_commits.items())),
        "evidence_type_counts": evidence_counts,
        "pit_audit": audit,
        "parallel_stage_receipts_verified": True,
        "future_prices_or_returns_used": False,
        "predictive_research_run": False,
        "holdout_run": False,
        "parameter_search_run": False,
    }
    summary = canonicalize_metadata(summary)
    if not isinstance(summary, dict):
        raise ValueError("issuer aggregate summary must be a mapping")

    combined_records.to_csv(out / "pit_evidence.csv", index=False)
    combined_coverage.to_csv(out / "pit_source_coverage.csv", index=False)
    combined_errors.to_csv(out / "pit_materialization_errors.csv", index=False)
    (out / "pit_materialization_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
