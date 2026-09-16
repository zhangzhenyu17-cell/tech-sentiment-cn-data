from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .data_akshare import download_universe_history, fetch_current_csindex_universe
from .index_history import read_adjustments_csv, read_anchor_csv, reconstruct_index_history
from .index_price import fetch_index_history
from .production_universe import (
    active_symbols_on,
    reconstruct_production_universe,
    validate_live_snapshot,
)
from .universe import current_snapshot_to_interval


def _add_history_download_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--adjust",
        default="",
        choices=["", "qfq", "hfq"],
        help="AKShare price adjustment; default is unadjusted.",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=["eastmoney", "tencent"],
        help=(
            "History provider; repeat to set fallback order. "
            "Default: eastmoney then tencent."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Retries per provider before falling back; default 1.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=0.75,
        help="Linear retry backoff base in seconds.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.15,
        help="Delay between stock requests to reduce provider throttling.",
    )
    parser.add_argument("--fail-fast", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare technology-universe price data without changing the sentiment model."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    current = sub.add_parser(
        "current-index",
        help="Fetch latest CSI constituents and historical prices (biased quick-validation mode).",
    )
    current.add_argument(
        "--index-code",
        action="append",
        required=True,
        help="CSI index code; repeat for multiple indices, e.g. --index-code 000688",
    )
    current.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    current.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    current.add_argument("--out-dir", default="data/prepared")
    _add_history_download_args(current)

    historical = sub.add_parser(
        "historical-index",
        help=(
            "Reconstruct a point-in-time index universe from an anchor snapshot and "
            "official in/out adjustments, then download all securities that were members."
        ),
    )
    historical.add_argument("--anchor-csv", required=True)
    historical.add_argument("--adjustments-csv", required=True)
    historical.add_argument("--anchor-effective-date", required=True, help="YYYY-MM-DD")
    historical.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    historical.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    historical.add_argument("--index-code", default="000688")
    historical.add_argument("--expected-constituents", type=int, default=50)
    historical.add_argument(
        "--variable-constituents",
        action="store_true",
        help=(
            "Allow the historical index constituent count to vary. "
            "Adjustment CSV rows may then leave in_symbol or out_symbol blank for "
            "unmatched additions/removals. Research mode only; fixed-size indices "
            "should keep the default fail-closed exact-count validation."
        ),
    )
    historical.add_argument("--out-dir", default="data/formal")
    _add_history_download_args(historical)

    production = sub.add_parser(
        "production-index",
        help=(
            "Resolve the trading-date-correct index universe from versioned anchors, "
            "cross-check the live constituent snapshot, then download member histories."
        ),
    )
    production.add_argument("--anchor-manifest", required=True)
    production.add_argument("--base-adjustments-csv", required=True)
    production.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    production.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    production.add_argument("--index-code", default="000688")
    production.add_argument("--expected-constituents", type=int, default=50)
    production.add_argument("--out-dir", default="data/daily")
    _add_history_download_args(production)
    return parser


def _providers(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(args.provider) if args.provider else ("eastmoney", "tencent")


def _download(universe, args: argparse.Namespace):
    return download_universe_history(
        universe,
        start_date=args.start_date,
        end_date=args.end_date,
        adjust=args.adjust,
        providers=_providers(args),
        retries=args.retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        sleep_seconds=args.sleep_seconds,
        fail_fast=args.fail_fast,
    )


def _current_index(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshot = fetch_current_csindex_universe(args.index_code)
    if snapshot.empty:
        raise SystemExit("No constituents returned for the requested index codes.")

    interval = current_snapshot_to_interval(
        snapshot,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    result = _download(interval, args)

    interval.to_csv(out_dir / "universe_current_snapshot.csv", index=False)
    result.prices.to_csv(out_dir / "prices.csv", index=False)
    result.errors.to_csv(out_dir / "download_errors.csv", index=False)

    print(
        f"current-snapshot universe: {interval['symbol'].nunique()} symbols; "
        f"price rows: {len(result.prices)}; download errors: {len(result.errors)}"
    )
    if len(result.prices) and "provider" in result.prices.columns:
        provider_counts = result.prices.groupby("provider")["symbol"].nunique().to_dict()
        print(f"provider symbol counts: {provider_counts}")
    print("WARNING: current-index mode has constituent survivorship/selection bias.")
    print("Use it only to validate the pipeline, not for formal 3-5 year performance claims.")
    print(f"outputs: {out_dir.resolve()}")


def _historical_index(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    anchor = read_anchor_csv(args.anchor_csv)
    adjustments = read_adjustments_csv(args.adjustments_csv)
    expected_constituents = None if args.variable_constituents else args.expected_constituents
    universe, segments, diagnostics = reconstruct_index_history(
        anchor,
        adjustments,
        history_start=args.start_date,
        history_end=args.end_date,
        anchor_effective_date=args.anchor_effective_date,
        expected_constituents=expected_constituents,
        index_code=args.index_code,
    )

    # Validate the lightweight official price dependency before the expensive
    # constituent-history batch so transient index-provider failures fail fast.
    index_prices = fetch_index_history(
        args.index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if index_prices.empty:
        raise SystemExit(
            f"No official index history returned for {args.index_code}; "
            "formal dual-price-rail research fails closed."
        )

    result = _download(universe, args)

    universe.to_csv(out_dir / "universe_point_in_time.csv", index=False, date_format="%Y-%m-%d")
    segments.to_csv(out_dir / "universe_segments.csv", index=False, date_format="%Y-%m-%d")
    result.prices.to_csv(out_dir / "prices.csv", index=False)
    result.errors.to_csv(out_dir / "download_errors.csv", index=False)
    index_prices.to_csv(out_dir / "index_prices.csv", index=False, date_format="%Y-%m-%d")

    print(
        "point-in-time reconstruction: "
        f"adjustment_dates={diagnostics.adjustment_dates} "
        f"adjustment_rows={diagnostics.adjustment_rows} "
        f"segments={diagnostics.segments} "
        f"unique_symbols={diagnostics.unique_symbols} "
        f"segment_count_range={diagnostics.min_segment_constituents}-"
        f"{diagnostics.max_segment_constituents} "
        f"variable_constituent_count={diagnostics.variable_constituent_count}"
    )
    print(
        f"downloaded price symbols={result.prices['symbol'].nunique() if len(result.prices) else 0}; "
        f"price rows={len(result.prices)}; download errors={len(result.errors)}"
    )
    print(
        f"official index rail: index_code={args.index_code} rows={len(index_prices)} "
        f"first_date={index_prices['date'].min().date()} "
        f"last_date={index_prices['date'].max().date()}"
    )
    if len(result.prices) and "provider" in result.prices.columns:
        provider_counts = result.prices.groupby("provider")["symbol"].nunique().to_dict()
        print(f"provider symbol counts: {provider_counts}")
    print(f"outputs: {out_dir.resolve()}")


def _production_index(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    universe, segments, anchor = reconstruct_production_universe(
        manifest_path=args.anchor_manifest,
        base_adjustments_path=args.base_adjustments_csv,
        history_start=args.start_date,
        history_end=args.end_date,
        index_code=args.index_code,
        expected_constituents=args.expected_constituents,
    )
    target = pd.Timestamp(args.end_date).normalize()
    active = active_symbols_on(universe, target)

    live = fetch_current_csindex_universe([args.index_code])
    if live.empty:
        raise SystemExit("No live constituents returned for production cross-check.")
    live_symbols = set(live["symbol"].astype(str).str.zfill(6))
    relation = validate_live_snapshot(
        manifest_path=args.anchor_manifest,
        target_date=target,
        active_symbols=active,
        live_symbols=live_symbols,
        expected_constituents=args.expected_constituents,
    )

    result = _download(universe, args)
    universe.to_csv(out_dir / "universe_point_in_time.csv", index=False, date_format="%Y-%m-%d")
    segments.to_csv(out_dir / "universe_segments.csv", index=False, date_format="%Y-%m-%d")
    live.to_csv(out_dir / "universe_live_snapshot.csv", index=False)
    result.prices.to_csv(out_dir / "prices.csv", index=False)
    result.errors.to_csv(out_dir / "download_errors.csv", index=False)

    metadata = {
        "target_date": target.date().isoformat(),
        "anchor_effective_date": anchor.effective_date.date().isoformat(),
        "anchor_file": anchor.anchor_path.name,
        "adjustment_files": [p.name for p in anchor.adjustment_paths],
        "active_symbols": len(active),
        "live_symbols": len(live_symbols),
        "live_snapshot_relation": relation,
        "universe_mode": "point_in_time",
    }
    (out_dir / "production_universe_meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(metadata, ensure_ascii=False))
    print(
        f"downloaded price symbols={result.prices['symbol'].nunique() if len(result.prices) else 0}; "
        f"price rows={len(result.prices)}; download errors={len(result.errors)}"
    )
    if len(result.prices) and "provider" in result.prices.columns:
        provider_counts = result.prices.groupby("provider")["symbol"].nunique().to_dict()
        print(f"provider symbol counts: {provider_counts}")
    print(f"outputs: {out_dir.resolve()}")


def main() -> None:
    args = _parser().parse_args()
    if args.command == "current-index":
        _current_index(args)
        return
    if args.command == "historical-index":
        _historical_index(args)
        return
    if args.command == "production-index":
        _production_index(args)
        return
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()

