from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from tech_sentiment.cninfo_direct import fetch_cninfo_announcements_direct
from tech_sentiment.official_pit_archives import (
    SSE_SOURCE_ID,
    SZSE_SOURCE_ID,
    materialize_sse_archive,
    materialize_szse_archive,
)
from tech_sentiment.pit_public_materialization import (
    CNINFO_SOURCE_ID,
    PitMaterializationResult,
    materialize_cninfo_archive,
    validate_materialized_pit_records,
)


UNMATERIALIZED_SOURCE_STATES = {
    "DERIVED_PIT_FUNDAMENTAL_TRENDS": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "DERIVED_PIT_TRAILING_VALUATION": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "OFFICIAL_POLICY_AND_REGULATORY_NOTICE_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
    "NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
}


def _symbols(value: str) -> list[str]:
    return sorted({item.strip().zfill(6) for item in value.split(",") if item.strip()})


def _symbols_from_csv(path: str | Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "symbol" not in frame.columns:
        raise SystemExit(f"symbol scope CSV missing symbol column: {path}")
    return sorted(
        {
            str(value).zfill(6)
            for value in frame["symbol"].dropna().astype(str)
            if len("".join(ch for ch in str(value) if ch.isdigit())) == 6
        }
    )


def _entity_suffix(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return ".SH"
    if symbol.startswith(("0", "1", "2", "3")):
        return ".SZ"
    if symbol.startswith(("4", "8")):
        return ".BJ"
    return ""


def _checkpoint_paths(root: Path, source: str, symbol: str) -> tuple[Path, Path, Path, Path]:
    base = root / source.lower() / symbol
    return (
        base / "records.csv",
        base / "coverage.csv",
        base / "errors.csv",
        base / "metadata.json",
    )


def _read_checkpoint(
    root: Path,
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> PitMaterializationResult | None:
    records_path, coverage_path, errors_path, metadata_path = _checkpoint_paths(root, source, symbol)
    if not metadata_path.is_file() or not coverage_path.is_file() or not records_path.is_file():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("source_identity") != source:
        return None
    if metadata.get("start_date") != start_date or metadata.get("end_date") != end_date:
        return None
    coverage = pd.read_csv(coverage_path)
    if coverage.empty or not coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all():
        return None
    records = pd.read_csv(records_path)
    if len(records):
        records = validate_materialized_pit_records(records)
    errors = pd.read_csv(errors_path) if errors_path.is_file() else pd.DataFrame(
        columns=["source_identity", "entity_id", "error"]
    )
    return PitMaterializationResult(records=records, coverage=coverage, errors=errors, summary=metadata)


def _write_checkpoint(root: Path, result: PitMaterializationResult, *, symbol: str) -> None:
    source = str(result.summary["source_identity"])
    records_path, coverage_path, errors_path, metadata_path = _checkpoint_paths(root, source, symbol)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    result.records.to_csv(records_path, index=False)
    result.coverage.to_csv(coverage_path, index=False)
    result.errors.to_csv(errors_path, index=False)
    metadata_path.write_text(
        json.dumps(result.summary, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _run_source(
    *,
    source: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    trading_dates: pd.Series,
    checkpoint_root: Path,
    materialize_one: Callable[[str], PitMaterializationResult],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    record_parts: list[pd.DataFrame] = []
    coverage_parts: list[pd.DataFrame] = []
    error_parts: list[pd.DataFrame] = []
    resumed = 0
    executed = 0
    for symbol in symbols:
        result = _read_checkpoint(
            checkpoint_root,
            source=source,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        if result is None:
            result = materialize_one(symbol)
            executed += 1
            _write_checkpoint(checkpoint_root, result, symbol=symbol)
        else:
            resumed += 1
        if len(result.records):
            record_parts.append(result.records)
        coverage_parts.append(result.coverage)
        if len(result.errors):
            error_parts.append(result.errors)

    records = (
        validate_materialized_pit_records(pd.concat(record_parts, ignore_index=True, sort=False))
        if record_parts
        else pd.DataFrame()
    )
    coverage = pd.concat(coverage_parts, ignore_index=True, sort=False) if coverage_parts else pd.DataFrame()
    errors = (
        pd.concat(error_parts, ignore_index=True, sort=False)
        if error_parts
        else pd.DataFrame(columns=["source_identity", "entity_id", "error"])
    )
    complete = int(coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").sum()) if len(coverage) else 0
    failed = int(coverage["query_status"].astype(str).eq("FAILED").sum()) if len(coverage) else len(symbols)
    state = (
        "QUALIFIED_INPUT"
        if symbols and complete == len(symbols) and failed == 0 and len(records) > 0
        else "PARTIAL_COVERAGE"
        if complete > 0
        else "DATA_INSUFFICIENT"
    )
    summary: dict[str, object] = {
        "source_identity": source,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": len(symbols),
        "complete_symbol_queries": complete,
        "failed_symbol_queries": failed,
        "materialized_records": int(len(records)),
        "resumed_symbol_queries": resumed,
        "executed_symbol_queries": executed,
        "readiness_state": state,
    }
    return records, coverage, errors, summary


def _audit_summary(records: pd.DataFrame) -> dict[str, object]:
    if records.empty:
        return {
            "records": 0,
            "required_fields_complete": False,
            "no_future_evidence": False,
            "duplicate_identity_free": False,
            "provenance_complete": False,
            "append_only_schema_ready": True,
            "prefix_replay_filter_equality": False,
            "revision_identity_complete": False,
        }
    validated = validate_materialized_pit_records(records)
    required_text = [
        "entity_id",
        "evidence_type",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "availability_state",
    ]
    provenance_ok = True
    for value in validated["provenance"]:
        try:
            payload = json.loads(str(value))
        except json.JSONDecodeError:
            provenance_ok = False
            break
        if not isinstance(payload, dict) or not payload.get("source_identity") or not payload.get("provider"):
            provenance_ok = False
            break
    ordered = validated.sort_values(
        ["evidence_available_date", "source_identity", "entity_id", "evidence_id"]
    ).reset_index(drop=True)
    prefix_ok = True
    for cutoff in ordered["evidence_available_date"].drop_duplicates().sort_values():
        prefix = ordered[ordered["evidence_available_date"].le(cutoff)].reset_index(drop=True)
        replay = ordered[ordered["evidence_available_date"].le(cutoff)].reset_index(drop=True)
        if not prefix.astype(str).equals(replay.astype(str)):
            prefix_ok = False
            break
    return {
        "records": int(len(validated)),
        "required_fields_complete": all(
            validated[column].notna().all() and validated[column].astype(str).str.strip().ne("").all()
            for column in required_text
        ),
        "no_future_evidence": bool(
            (validated["evidence_available_date"] >= validated["event_date"]).all()
        ),
        "duplicate_identity_free": bool(
            not validated["evidence_id"].duplicated().any()
            and not validated["ingestion_identity"].duplicated().any()
        ),
        "provenance_complete": provenance_ok,
        "append_only_schema_ready": True,
        "prefix_replay_filter_equality": prefix_ok,
        "revision_identity_complete": bool(validated["revision_id"].astype(str).str.strip().ne("").all()),
        "earliest_evidence_available_date": str(validated["evidence_available_date"].min().date()),
        "latest_evidence_available_date": str(validated["evidence_available_date"].max().date()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual-only PIT evidence materialization from registered public sources."
    )
    parser.add_argument("--symbols", default="", help="Optional comma-separated six-digit A-share codes")
    parser.add_argument("--symbols-csv", default="", help="Optional CSV with a symbol column")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-csv", required=True, help="Real trading calendar CSV from the same Capital qualification run")
    parser.add_argument("--calendar-date-column", default="date")
    parser.add_argument("--checkpoint-dir", default=".cache/capital_pit_v4a")
    parser.add_argument("--out-dir", default="output/pit_evidence_materialization")
    args = parser.parse_args()

    symbols = _symbols(args.symbols)
    if args.symbols_csv:
        symbols = sorted(set(symbols) | set(_symbols_from_csv(args.symbols_csv)))
    if not symbols:
        raise SystemExit("at least one symbol is required via --symbols or --symbols-csv")

    calendar_frame = pd.read_csv(args.calendar_csv)
    if args.calendar_date_column not in calendar_frame.columns:
        raise SystemExit(f"calendar missing date column: {args.calendar_date_column}")
    trading_dates = pd.to_datetime(
        calendar_frame[args.calendar_date_column], errors="raise"
    ).dt.normalize()
    if trading_dates.empty:
        raise SystemExit("real trading calendar is empty")

    start_date = str(pd.Timestamp(args.start_date).date())
    end_date = str(pd.Timestamp(args.end_date).date())
    checkpoint_root = Path(args.checkpoint_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sh_symbols = [symbol for symbol in symbols if _entity_suffix(symbol) == ".SH"]
    sz_symbols = [symbol for symbol in symbols if _entity_suffix(symbol) == ".SZ"]

    cninfo = _run_source(
        source=CNINFO_SOURCE_ID,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        trading_dates=trading_dates,
        checkpoint_root=checkpoint_root,
        materialize_one=lambda symbol: materialize_cninfo_archive(
            [symbol],
            start_date=start_date,
            end_date=end_date,
            trading_dates=trading_dates,
            fetcher=fetch_cninfo_announcements_direct,
        ),
    )
    sse = _run_source(
        source=SSE_SOURCE_ID,
        symbols=sh_symbols,
        start_date=start_date,
        end_date=end_date,
        trading_dates=trading_dates,
        checkpoint_root=checkpoint_root,
        materialize_one=lambda symbol: materialize_sse_archive(
            [symbol], start_date=start_date, end_date=end_date, trading_dates=trading_dates
        ),
    )
    szse = _run_source(
        source=SZSE_SOURCE_ID,
        symbols=sz_symbols,
        start_date=start_date,
        end_date=end_date,
        trading_dates=trading_dates,
        checkpoint_root=checkpoint_root,
        materialize_one=lambda symbol: materialize_szse_archive(
            [symbol], start_date=start_date, end_date=end_date, trading_dates=trading_dates
        ),
    )

    source_results = {
        CNINFO_SOURCE_ID: cninfo,
        SSE_SOURCE_ID: sse,
        SZSE_SOURCE_ID: szse,
    }
    all_records = [result[0] for result in source_results.values() if len(result[0])]
    all_coverage = [result[1] for result in source_results.values() if len(result[1])]
    all_errors = [result[2] for result in source_results.values() if len(result[2])]
    records = (
        validate_materialized_pit_records(pd.concat(all_records, ignore_index=True, sort=False))
        if all_records
        else pd.DataFrame()
    )
    coverage = pd.concat(all_coverage, ignore_index=True, sort=False) if all_coverage else pd.DataFrame()
    errors = (
        pd.concat(all_errors, ignore_index=True, sort=False)
        if all_errors
        else pd.DataFrame(columns=["source_identity", "entity_id", "error"])
    )

    source_states = {
        source: str(result[3]["readiness_state"]) for source, result in source_results.items()
    }
    source_states.update(UNMATERIALIZED_SOURCE_STATES)
    evidence_counts = (
        records["evidence_type"].astype(str).value_counts().sort_index().to_dict()
        if len(records)
        else {}
    )
    audit = _audit_summary(records)
    summary = {
        "schema_version": "capital-pit-public-ledger-v4a",
        "status": "MATERIALIZED_PARTIAL" if len(records) else "NO_RECORDS_MATERIALIZED",
        "start_date": start_date,
        "end_date": end_date,
        "symbols": len(symbols),
        "sh_symbols": len(sh_symbols),
        "sz_symbols": len(sz_symbols),
        "materialized_records": int(len(records)),
        "failed_symbol_queries": int(sum(int(result[3]["failed_symbol_queries"]) for result in source_results.values())),
        "source_states": source_states,
        "source_summaries": {source: result[3] for source, result in source_results.items()},
        "evidence_type_counts": evidence_counts,
        "pit_audit": audit,
        "major_negative_event_exclusion_complete": False,
        "major_negative_reason": "OFFICIAL_POLICY_REGULATORY_AND_NEGATIVE_EVENT_REVIEW_NOT_MATERIALIZED",
        "future_prices_or_returns_used": False,
        "predictive_research_run": False,
        "holdout_run": False,
        "parameter_search_run": False,
    }

    records.to_csv(out / "pit_evidence.csv", index=False)
    coverage.to_csv(out / "pit_source_coverage.csv", index=False)
    errors.to_csv(out / "pit_materialization_errors.csv", index=False)
    for source, result in source_results.items():
        slug = source.lower()
        result[0].to_csv(out / f"{slug}_evidence.csv", index=False)
        result[1].to_csv(out / f"{slug}_coverage.csv", index=False)
        result[2].to_csv(out / f"{slug}_errors.csv", index=False)
    (out / "pit_materialization_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
