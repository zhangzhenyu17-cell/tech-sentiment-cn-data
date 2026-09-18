from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

import pandas as pd

from tech_sentiment.canonical_materialization import (
    canonicalize_frame,
    canonicalize_metadata,
)
from tech_sentiment.cninfo_direct import fetch_cninfo_announcements_direct
from tech_sentiment.immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from tech_sentiment.official_pit_archives import (
    SSE_SOURCE_ID,
    SZSE_SOURCE_ID,
    fetch_sse_announcements,
    fetch_szse_announcements,
    materialize_sse_archive,
    materialize_szse_archive,
)
from tech_sentiment.pit_public_materialization import (
    CNINFO_SOURCE_ID,
    PitMaterializationResult,
    materialize_cninfo_archive,
    validate_materialized_pit_records,
)
from tech_sentiment.pit_replay_audit import audit_pit_replay


ISSUER_PIT_CHECKPOINT_VERSION = "issuer-pit-exact-identity-v3-success-only"


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


def _shard(values: list[str], *, index: int, count: int) -> list[str]:
    if count < 1:
        raise ValueError("shard-count must be >= 1")
    if index < 0 or index >= count:
        raise ValueError("shard-index must satisfy 0 <= index < shard-count")
    ordered = sorted(values)
    return [value for position, value in enumerate(ordered) if position % count == index]


def _publication_has_precise_clock(value: object) -> bool:
    return bool(re.search(r"(?:^|\s)\d{1,2}:\d{2}(?::\d{2})?(?:\s|$)", str(value).strip()))


def filter_records_available_by_asof(
    frame: pd.DataFrame,
    *,
    publication_column: str,
    trading_dates: pd.Series,
) -> tuple[pd.DataFrame, int]:
    if publication_column not in frame.columns:
        raise ValueError(f"publication column missing: {publication_column}")
    if frame.empty:
        return frame.copy(), 0
    calendar = pd.DatetimeIndex(pd.to_datetime(trading_dates, errors="raise")).normalize().sort_values().unique()
    if not len(calendar):
        raise ValueError("real trading calendar is empty")
    final_market_date = pd.Timestamp(calendar[-1]).normalize()
    keep: list[bool] = []
    for value in frame[publication_column]:
        publication = pd.Timestamp(pd.to_datetime(value, errors="raise"))
        publication_date = publication.normalize()
        if publication_date < final_market_date:
            keep.append(True)
            continue
        if publication_date > final_market_date:
            keep.append(False)
            continue
        precise = _publication_has_precise_clock(value)
        at_or_before_close = precise and (
            publication.hour < 15
            or (
                publication.hour == 15
                and publication.minute == 0
                and publication.second == 0
            )
        )
        keep.append(at_or_before_close)
    mask = pd.Series(keep, index=frame.index, dtype=bool)
    return frame.loc[mask].copy().reset_index(drop=True), int((~mask).sum())


def _calendar_identity(trading_dates: pd.Series) -> str:
    dates = [
        str(pd.Timestamp(value).normalize().date())
        for value in pd.to_datetime(trading_dates, errors="raise")
    ]
    return hashlib.sha256("\n".join(dates).encode("utf-8")).hexdigest()


def _checkpoint_identity(
    *,
    source: str,
    symbol: str,
    start_date: str,
    end_date: str,
    source_commit: str,
    calendar_identity: str,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="issuer-pit-archive",
        producer_version=ISSUER_PIT_CHECKPOINT_VERSION,
        source_commit=source_commit,
        source_identities=(source,),
        query_identity={"symbol": symbol, "source_identity": source},
        scope={
            "start_date": start_date,
            "end_date": end_date,
            "calendar_identity": calendar_identity,
        },
    )


def _with_tail_filter(result: PitMaterializationResult, *, omitted: int) -> PitMaterializationResult:
    summary = canonicalize_metadata(result.summary)
    if not isinstance(summary, dict):
        raise ValueError("issuer PIT source summary must be a mapping")
    summary["tail_records_beyond_asof_not_materialized"] = int(omitted)
    summary["tail_handling"] = "OMIT_UNTIL_NEXT_REAL_TRADING_DATE_EXISTS_NO_FILL_NO_BACKFILL"
    records = canonicalize_frame(result.records)
    coverage = canonicalize_frame(result.coverage)
    if len(records):
        records = validate_materialized_pit_records(records)
    return PitMaterializationResult(
        records=records,
        coverage=coverage,
        errors=result.errors.copy(),
        summary=summary,
    )


def _run_source(
    *,
    source: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    source_commit: str,
    calendar_identity: str,
    store: ImmutableCheckpointStore,
    materialize_one: Callable[[str], PitMaterializationResult],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    record_parts: list[pd.DataFrame] = []
    coverage_parts: list[pd.DataFrame] = []
    error_parts: list[pd.DataFrame] = []
    resumed = 0
    executed = 0
    tail_omitted = 0
    for symbol in symbols:
        identity = _checkpoint_identity(
            source=source,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            source_commit=source_commit,
            calendar_identity=calendar_identity,
        )
        loaded = store.load(identity)
        if loaded is not None:
            result = PitMaterializationResult(
                records=canonicalize_frame(loaded.frames["records"]),
                coverage=canonicalize_frame(loaded.frames["coverage"]),
                errors=loaded.frames["errors"],
                summary=dict(loaded.receipt.get("metadata", {}).get("summary") or {}),
            )
            cached_complete = (
                len(result.coverage) > 0
                and "query_status" in result.coverage.columns
                and result.coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all()
                and len(result.errors) == 0
            )
            if not cached_complete:
                raise ValueError(
                    "issuer checkpoint must contain only successful COMPLETE_WINDOW results"
                )
            if len(result.records):
                result = PitMaterializationResult(
                    records=validate_materialized_pit_records(result.records),
                    coverage=result.coverage,
                    errors=result.errors,
                    summary=result.summary,
                )
            resumed += 1
        else:
            result = materialize_one(symbol)
            stable_summary = canonicalize_metadata(result.summary)
            if not isinstance(stable_summary, dict):
                raise ValueError("issuer PIT checkpoint summary must be a mapping")
            result = PitMaterializationResult(
                records=canonicalize_frame(result.records),
                coverage=canonicalize_frame(result.coverage),
                errors=result.errors,
                summary=stable_summary,
            )
            checkpointable = (
                len(result.coverage) > 0
                and "query_status" in result.coverage.columns
                and result.coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").all()
                and len(result.errors) == 0
            )
            if checkpointable:
                store.save(
                    identity,
                    frames={
                        "records": result.records,
                        "coverage": result.coverage,
                        "errors": result.errors,
                    },
                    metadata={"summary": stable_summary},
                )
            executed += 1
        tail_omitted += int(result.summary.get("tail_records_beyond_asof_not_materialized") or 0)
        if len(result.records):
            record_parts.append(result.records)
        if len(result.coverage):
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
    if symbols and complete == len(symbols) and failed == 0:
        state = "QUALIFIED_INPUT"
    elif complete > 0:
        state = "PARTIAL_COVERAGE"
    else:
        state = "DATA_INSUFFICIENT"
    summary_with_runtime: dict[str, object] = {
        "source_identity": source,
        "start_date": start_date,
        "end_date": end_date,
        "source_commit": source_commit,
        "calendar_identity": calendar_identity,
        "symbols": len(symbols),
        "complete_symbol_queries": complete,
        "failed_symbol_queries": failed,
        "materialized_records": int(len(records)),
        "tail_records_beyond_asof_not_materialized": tail_omitted,
        "resumed_symbol_queries": resumed,
        "executed_symbol_queries": executed,
        "checkpoint_schema": ISSUER_PIT_CHECKPOINT_VERSION,
        "readiness_state": state,
    }
    summary = canonicalize_metadata(summary_with_runtime)
    if not isinstance(summary, dict):
        raise ValueError("issuer PIT summary must be a mapping")
    return records, coverage, errors, summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual-only exact-identity PIT issuer archive materialization."
    )
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbols-csv", default="")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-csv", required=True)
    parser.add_argument("--calendar-date-column", default="date")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--source",
        choices=("ALL", CNINFO_SOURCE_ID, SSE_SOURCE_ID, SZSE_SOURCE_ID),
        default="ALL",
    )
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--checkpoint-dir", default=".cache/capital_pit_v4a/issuer")
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
    calendar_identity = _calendar_identity(trading_dates)
    store = ImmutableCheckpointStore(args.checkpoint_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sh_symbols = [symbol for symbol in symbols if _entity_suffix(symbol) == ".SH"]
    sz_symbols = [symbol for symbol in symbols if _entity_suffix(symbol) == ".SZ"]

    def cninfo_one(symbol: str) -> PitMaterializationResult:
        omitted = 0

        def fetcher(**kwargs: object) -> pd.DataFrame:
            nonlocal omitted
            raw = fetch_cninfo_announcements_direct(**kwargs)
            filtered, omitted = filter_records_available_by_asof(
                raw, publication_column="公告时间", trading_dates=trading_dates
            )
            return filtered

        return _with_tail_filter(
            materialize_cninfo_archive(
                [symbol],
                start_date=start_date,
                end_date=end_date,
                trading_dates=trading_dates,
                fetcher=fetcher,
            ),
            omitted=omitted,
        )

    def sse_one(symbol: str) -> PitMaterializationResult:
        omitted = 0

        def fetcher(**kwargs: object) -> pd.DataFrame:
            nonlocal omitted
            raw = fetch_sse_announcements(**kwargs)
            filtered, omitted = filter_records_available_by_asof(
                raw, publication_column="publication_time", trading_dates=trading_dates
            )
            return filtered

        return _with_tail_filter(
            materialize_sse_archive(
                [symbol],
                start_date=start_date,
                end_date=end_date,
                trading_dates=trading_dates,
                fetcher=fetcher,
            ),
            omitted=omitted,
        )

    def szse_one(symbol: str) -> PitMaterializationResult:
        omitted = 0

        def fetcher(**kwargs: object) -> pd.DataFrame:
            nonlocal omitted
            raw = fetch_szse_announcements(**kwargs)
            filtered, omitted = filter_records_available_by_asof(
                raw, publication_column="publication_time", trading_dates=trading_dates
            )
            return filtered

        return _with_tail_filter(
            materialize_szse_archive(
                [symbol],
                start_date=start_date,
                end_date=end_date,
                trading_dates=trading_dates,
                fetcher=fetcher,
            ),
            omitted=omitted,
        )

    source_specs = {
        CNINFO_SOURCE_ID: (symbols, cninfo_one),
        SSE_SOURCE_ID: (sh_symbols, sse_one),
        SZSE_SOURCE_ID: (sz_symbols, szse_one),
    }
    selected_sources = (
        list(source_specs)
        if args.source == "ALL"
        else [str(args.source)]
    )
    source_results: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]] = {}
    for source in selected_sources:
        source_symbols, materialize_one = source_specs[source]
        shard_symbols = _shard(
            list(source_symbols),
            index=int(args.shard_index),
            count=int(args.shard_count),
        )
        if not shard_symbols:
            raise SystemExit(
                f"issuer shard has no applicable symbols: source={source} "
                f"index={args.shard_index} count={args.shard_count}"
            )
        source_results[source] = _run_source(
            source=source,
            symbols=shard_symbols,
            start_date=start_date,
            end_date=end_date,
            source_commit=args.source_commit,
            calendar_identity=calendar_identity,
            store=store,
            materialize_one=materialize_one,
        )
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

    # Runtime-only diagnostic: preserve stable canonical summaries while making
    # provider/protocol failures immediately visible in Actions logs. Only the
    # exception class prefix is emitted; raw transport text remains in the
    # per-source errors CSV and does not enter canonical qualification metadata.
    for source, result in source_results.items():
        source_errors = result[2]
        if len(source_errors) and "error" in source_errors.columns:
            error_types = (
                source_errors["error"]
                .astype(str)
                .str.split(":", n=1)
                .str[0]
                .value_counts()
                .sort_index()
                .to_dict()
            )
            print(
                json.dumps(
                    {
                        "diagnostic": "ISSUER_SOURCE_ERROR_TYPES",
                        "source_identity": source,
                        "failed_rows": int(len(source_errors)),
                        "error_type_counts": {
                            str(key): int(value) for key, value in error_types.items()
                        },
                        "canonical_summary_unchanged": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    evidence_counts = (
        records["evidence_type"].astype(str).value_counts().sort_index().to_dict()
        if len(records)
        else {}
    )
    audit = audit_pit_replay(records) if len(records) else {
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
    summary = {
        "schema_version": "capital-pit-public-issuer-ledger-v4a2",
        "status": "PUBLIC_ISSUER_PIT_MATERIALIZATION_COMPLETED",
        "start_date": start_date,
        "end_date": end_date,
        "source_commit": args.source_commit,
        "calendar_identity": calendar_identity,
        "symbols": len(symbols),
        "sh_symbols": len(sh_symbols),
        "sz_symbols": len(sz_symbols),
        "selected_sources": selected_sources,
        "shard_index": int(args.shard_index),
        "shard_count": int(args.shard_count),
        "materialized_records": int(len(records)),
        "failed_symbol_queries": int(sum(int(result[3]["failed_symbol_queries"]) for result in source_results.values())),
        "source_states": source_states,
        "source_summaries": {source: result[3] for source, result in source_results.items()},
        "evidence_type_counts": evidence_counts,
        "pit_audit": audit,
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
