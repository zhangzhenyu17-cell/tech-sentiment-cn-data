from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from tech_sentiment.capital_input_data import (
    SSE_ETF_SHARE_SOURCE_ID,
    SSE_ETF_SHARE_SOURCE_URL,
    SSE_TURNOVER_SOURCE_ID,
    SSE_TURNOVER_SOURCE_URL,
    SZSE_TURNOVER_SOURCE_ID,
    SZSE_TURNOVER_SOURCE_URL,
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
    qualify_trailing_etf_coverage,
)
from tech_sentiment.financing_materialization import materialize_financing_history
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.materialization_manifest import MaterializedAsset, file_sha256, write_manifest
from tech_sentiment.pit_public_data import CNINFO_ARCHIVE_URL, CNINFO_PROVIDER, CNINFO_SOURCE_ID, fetch_cninfo_announcement_history
from tech_sentiment.pit_replay import assert_prefix_replay_equality, validate_pit_ledger


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _asset(
    *,
    name: str,
    path: Path,
    rows: int,
    state: str,
    source_identity: str,
    provider: str,
    provenance: str,
    revision_semantics: str,
) -> MaterializedAsset:
    digest = file_sha256(path)
    return MaterializedAsset(
        name=name,
        path=path.name,
        sha256=digest,
        rows=rows,
        state=state,
        source_identity=source_identity,
        provider=provider,
        provenance=provenance,
        immutable_version=f"sha256:{digest}",
        revision_semantics=revision_semantics,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual-only Capital/PIT public-data materialization.")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--fund-code", default="588000")
    parser.add_argument("--calendar-index-code", default="000906")
    parser.add_argument(
        "--pit-entities",
        default="",
        help="Comma-separated public issuer ids such as 600276.SH,000001.SZ. Empty means do not invent a PIT universe.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--out-dir", default="output/capital_pit_materialization")
    args = parser.parse_args()

    if args.end_date is None:
        args.end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    generated_at = datetime.now(ZoneInfo("UTC")).isoformat()
    repository_sha = os.environ.get("GITHUB_SHA", "LOCAL_UNPINNED")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    calendar_frame = fetch_index_history(
        args.calendar_index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if calendar_frame.empty:
        raise SystemExit("trading calendar source returned no rows")
    calendar = (
        pd.DatetimeIndex(pd.to_datetime(calendar_frame["date"], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if str(pd.Timestamp(calendar[0]).date()) != str(pd.Timestamp(args.start_date).date()):
        raise SystemExit(
            f"real trading calendar does not start at requested target start: {calendar[0]} != {args.start_date}"
        )
    calendar_path = out / "trading_calendar.csv"
    _write_csv(calendar_frame, calendar_path)

    etf = fetch_sse_etf_share_history(
        trading_dates=calendar,
        fund_codes=[args.fund_code],
        sleep_seconds=args.sleep_seconds,
    )
    coverage = qualify_trailing_etf_coverage(
        etf.data,
        trading_dates=calendar,
        fund_code=args.fund_code,
        window=60,
        min_coverage=0.80,
    )
    aligned_shares = coverage.set_index("date")["fund_shares"]
    coverage["endpoint_20d_available"] = coverage["fund_shares"].notna() & aligned_shares.shift(20).reset_index(drop=True).notna()
    coverage["endpoint_60d_available"] = coverage["fund_shares"].notna() & aligned_shares.shift(60).reset_index(drop=True).notna()
    mature = coverage["coverage"].notna()
    etf_prestart_ok = bool(len(coverage) and coverage.iloc[0]["observed"])
    etf_windows_ok = bool(mature.any() and coverage.loc[mature, "coverage"].ge(0.80).all())
    etf_state = "QUALIFIED_INPUT" if etf_prestart_ok and etf_windows_ok else "PARTIAL_COVERAGE"
    etf_path = out / "sse_588000_etf_shares.csv"
    etf_errors_path = out / "sse_588000_etf_share_errors.csv"
    etf_coverage_path = out / "sse_588000_etf_share_coverage.csv"
    _write_csv(etf.data, etf_path)
    _write_csv(etf.errors, etf_errors_path)
    _write_csv(coverage, etf_coverage_path)

    turnover = fetch_sse_szse_a_share_turnover_history(
        trading_dates=calendar,
        sleep_seconds=args.sleep_seconds,
    )
    turnover_path = out / "sse_szse_a_share_turnover.csv"
    turnover_errors_path = out / "sse_szse_turnover_errors.csv"
    _write_csv(turnover.combined, turnover_path)
    _write_csv(turnover.errors, turnover_errors_path)
    turnover_coverage = float(len(turnover.combined) / len(calendar)) if len(calendar) else 0.0
    turnover_state = "QUALIFIED_INPUT" if len(turnover.combined) == len(calendar) else "PARTIAL_COVERAGE"

    financing = materialize_financing_history(
        trading_dates=calendar,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    financing_raw_path = out / "financing_raw_aligned.csv"
    financing_canonical_path = out / "financing_canonical_cny.csv"
    financing_errors_path = out / "financing_errors.csv"
    _write_csv(financing.raw_aligned, financing_raw_path)
    _write_csv(financing.canonical, financing_canonical_path)
    _write_csv(financing.errors, financing_errors_path)

    pit_entities = sorted({value.strip() for value in args.pit_entities.split(",") if value.strip()})
    pit_path = out / "cninfo_pit_announcements.csv"
    pit_errors_path = out / "cninfo_pit_errors.csv"
    pit_state = "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED"
    pit_records = pd.DataFrame()
    pit_errors = pd.DataFrame(columns=["entity_id", "error"])
    if pit_entities:
        # Reserve the last observed real trading date as the next-date anchor for
        # date-only/after-close publication timestamps. This prevents the latest
        # calendar row from being silently treated as same-day evidence.
        if len(calendar) < 2:
            raise SystemExit("PIT materialization requires at least two real trading dates")
        pit_end = str(pd.Timestamp(calendar[-2]).date())
        pit = fetch_cninfo_announcement_history(
            entity_ids=pit_entities,
            start_date=args.start_date,
            end_date=pit_end,
            trading_dates=calendar,
            ingestion_timestamp=generated_at,
            repository_sha=repository_sha,
        )
        pit_records = pit.data
        pit_errors = pit.errors
        if len(pit_records):
            pit_records = validate_pit_ledger(pit_records)
            assert_prefix_replay_equality(pit_records, calendar[len(calendar) // 2])
        pit_state = (
            "QUALIFIED_INPUT"
            if len(pit_records) and pit_errors.empty
            else "PARTIAL_COVERAGE"
            if len(pit_records)
            else "DATA_INSUFFICIENT"
        )
    _write_csv(pit_records, pit_path)
    _write_csv(pit_errors, pit_errors_path)

    assets = [
        _asset(
            name="588000_long_flow_input",
            path=etf_path,
            rows=len(etf.data),
            state=etf_state,
            source_identity=SSE_ETF_SHARE_SOURCE_ID,
            provider="Shanghai Stock Exchange via AKShare",
            provenance=SSE_ETF_SHARE_SOURCE_URL,
            revision_semantics="immutable captured artifact; later official changes create a new artifact hash",
        ),
        _asset(
            name="sse_szse_a_share_turnover",
            path=turnover_path,
            rows=len(turnover.combined),
            state=turnover_state,
            source_identity=f"{SSE_TURNOVER_SOURCE_ID}+{SZSE_TURNOVER_SOURCE_ID}",
            provider="SSE+SZSE official market overview via AKShare",
            provenance=f"{SSE_TURNOVER_SOURCE_URL};{SZSE_TURNOVER_SOURCE_URL}",
            revision_semantics="bilateral same-day artifact; missing either exchange fails qualification",
        ),
        _asset(
            name="financing_research_input",
            path=financing_raw_path,
            rows=len(financing.raw_aligned),
            state=str(financing.summary["state"]),
            source_identity="SSE_MARGIN_SUMMARY+SZSE_MARGIN_SUMMARY",
            provider="SSE+SZSE official margin summaries via AKShare",
            provenance="SSE raw CNY; SZSE raw CNY_100M; canonical CNY; no inferred multiplier",
            revision_semantics="immutable captured artifact; bilateral completeness required",
        ),
        _asset(
            name="cninfo_pit_announcements",
            path=pit_path,
            rows=len(pit_records),
            state=pit_state,
            source_identity=CNINFO_SOURCE_ID,
            provider=CNINFO_PROVIDER,
            provenance=CNINFO_ARCHIVE_URL,
            revision_semantics="append-only by immutable document id; later documents never overwrite earlier market-date replay",
        ),
    ]
    manifest = write_manifest(
        assets,
        output_path=out / "materialization_manifest.json",
        repository_sha=repository_sha,
        generated_at=generated_at,
    )

    readiness = {
        "schema_version": "capital-pit-readiness-v1",
        "target_start": str(pd.Timestamp(calendar[0]).date()),
        "target_end": str(pd.Timestamp(calendar[-1]).date()),
        "trading_days": int(len(calendar)),
        "readiness": {
            "588000_long_flow": etf_state,
            "SSE_SZSE_A_SHARES_turnover": turnover_state,
            "financing": str(financing.summary["state"]),
            "fundamental_PIT": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "earnings_PIT": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "valuation_PIT": "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "major_event_PIT": pit_state if pit_entities else "HISTORICAL_RECONSTRUCTABLE_NOT_MATERIALIZED",
            "major_negative_exclusion": "DATA_INSUFFICIENT",
            "clean_forward_external_evidence": "DATA_INSUFFICIENT",
        },
        "588000": {
            "observed_days": int(coverage["observed"].sum()),
            "raw_coverage": float(coverage["observed"].mean()),
            "eligible_days": int(coverage["eligible"].sum()),
            "endpoint_20d_days": int(coverage["endpoint_20d_available"].sum()),
            "endpoint_60d_days": int(coverage["endpoint_60d_available"].sum()),
            "trailing60_gate": 0.80,
            "no_fill": True,
        },
        "turnover": {
            "complete_days": int(len(turnover.combined)),
            "coverage": turnover_coverage,
            "scope": "SSE_SZSE_A_SHARES",
            "unit": "CNY",
        },
        "financing": financing.summary,
        "pit": {
            "requested_entities": pit_entities,
            "records": int(len(pit_records)),
            "errors": int(len(pit_errors)),
            "raw_issuer_announcements_only": True,
            "fundamental_or_event_direction_inferred": False,
            "major_negative_event_exclusion_complete": False,
        },
        "production_or_model_output": False,
        "manifest_sha256": file_sha256(out / "materialization_manifest.json"),
        "manifest_assets": len(manifest["assets"]),
    }
    (out / "readiness_matrix.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(readiness, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
