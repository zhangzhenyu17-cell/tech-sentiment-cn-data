from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path
from typing import Iterable

from tech_sentiment.eastmoney_fund_holdings_evidence import (
    TRACKING_ETF_931152,
    FundHoldingBatch,
    fetch_and_parse_holdings_year,
)
from tech_sentiment.sina_index_membership_evidence import (
    IndexMembershipInterval,
    fetch_related_page,
    parse_membership_intervals,
)


STATUS = "SECONDARY_RECONSTRUCTION_CANDIDATE_ONLY"

PERIODS = (
    ("2020-06", "2020-06-15", "2020-06-30"),
    ("2020-12", "2020-12-14", "2020-12-31"),
    ("2021-06", "2021-06-15", "2021-06-30"),
    ("2021-12", "2021-12-13", "2021-12-31"),
    ("2022-06", "2022-06-13", "2022-06-30"),
    ("2022-12", "2022-12-12", "2022-12-31"),
    ("2023-06", "2023-06-12", "2023-06-30"),
    ("2023-12", "2023-12-11", "2023-12-31"),
)


def _dump_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _fetch_sina_symbol(symbol: str, timeout: int) -> tuple[
    str, list[IndexMembershipInterval], str, str, str | None
]:
    try:
        html, url, digest = fetch_related_page(symbol, timeout=timeout)
        intervals = parse_membership_intervals(
            html,
            symbol=symbol,
            source_url=url,
            response_sha256=digest,
        )
        return symbol, intervals, html, digest, None
    except Exception as exc:
        return symbol, [], "", "", f"{type(exc).__name__}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free secondary reconstruction probe for CSI 931152. "
            "EastMoney/Tiantian 159992 holdings supply a broad candidate pool; "
            "Sina stock pages independently supply 931152 membership intervals."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 12:
        raise SystemExit("workers must be between 1 and 12")

    out_dir = args.output_dir
    eastmoney_raw_dir = out_dir / "raw_eastmoney"
    sina_raw_dir = out_dir / "raw_sina"
    eastmoney_raw_dir.mkdir(parents=True, exist_ok=True)
    sina_raw_dir.mkdir(parents=True, exist_ok=True)

    all_batches: list[FundHoldingBatch] = []
    eastmoney_failures: list[dict[str, object]] = []
    for year in range(2020, 2024):
        try:
            batches, _years, raw_text = fetch_and_parse_holdings_year(
                TRACKING_ETF_931152,
                year,
                topline=100,
                timeout=args.timeout,
            )
            all_batches.extend(batches)
            (eastmoney_raw_dir / f"159992_{year}.txt").write_text(
                raw_text,
                encoding="utf-8",
            )
        except Exception as exc:
            eastmoney_failures.append(
                {"year": year, "error": f"{type(exc).__name__}: {exc}"}
            )

    candidate_sets = {
        batch.report_date: set(batch.symbols)
        for batch in all_batches
        if batch.full_report_candidate_set
    }
    candidate_union: set[str] = set()
    for symbols in candidate_sets.values():
        candidate_union.update(symbols)

    intervals_by_symbol: dict[str, list[IndexMembershipInterval]] = {}
    fetch_audit: list[dict[str, object]] = []
    interval_rows: list[dict[str, object]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_fetch_sina_symbol, symbol, args.timeout): symbol
            for symbol in sorted(candidate_union)
        }
        for future in as_completed(futures):
            symbol, intervals, html, digest, error = future.result()
            if error is not None:
                fetch_audit.append(
                    {
                        "symbol": symbol,
                        "status": "failed_closed",
                        "error": error,
                    }
                )
                continue
            (sina_raw_dir / f"{symbol}.html").write_text(html, encoding="utf-8")
            intervals_by_symbol[symbol] = intervals
            fetch_audit.append(
                {
                    "symbol": symbol,
                    "status": "ok_with_931152_interval" if intervals else "ok_no_931152_interval",
                    "response_sha256": digest,
                    "interval_count": len(intervals),
                }
            )
            interval_rows.extend(asdict(interval) for interval in intervals)

    successful_symbols = {row["symbol"] for row in fetch_audit if str(row["status"]).startswith("ok_")}
    failed_symbols = sorted(candidate_union - successful_symbols)
    reconstruction_rows: list[dict[str, object]] = []

    for expected_period, effective_date, report_date in PERIODS:
        report_candidates = candidate_sets.get(report_date, set())
        active: set[str] = set()
        for symbol, intervals in intervals_by_symbol.items():
            if any(interval.active_on(effective_date) for interval in intervals):
                active.add(symbol)
        reconstruction_rows.append(
            {
                "expected_period": expected_period,
                "effective_date": effective_date,
                "candidate_report_date": report_date,
                "candidate_report_size": len(report_candidates),
                "reconstructed_size": len(active),
                "reconstructed_symbols": sorted(active),
                "reconstructed_missing_from_same_report": sorted(active - report_candidates),
                "same_report_nonmembers": sorted(report_candidates - active),
                "formal_manifest_qualified": False,
            }
        )

    _dump_jsonl(
        out_dir / "eastmoney_batches.jsonl",
        [
            {
                **asdict(batch),
                "symbols": list(batch.symbols),
                "symbol_count": len(batch.symbols),
            }
            for batch in all_batches
        ],
    )
    _dump_jsonl(out_dir / "sina_fetch_audit.jsonl", sorted(fetch_audit, key=lambda row: str(row["symbol"])))
    _dump_jsonl(out_dir / "sina_931152_intervals.jsonl", interval_rows)
    _dump_jsonl(out_dir / "reconstructed_sets.jsonl", reconstruction_rows)
    _dump_jsonl(out_dir / "eastmoney_failures.jsonl", eastmoney_failures)

    coverage = (
        len(successful_symbols) / len(candidate_union)
        if candidate_union
        else 0.0
    )
    report = {
        "status": STATUS,
        "target_index": "931152",
        "fund_code": TRACKING_ETF_931152,
        "candidate_union_size": len(candidate_union),
        "sina_fetch_successes": len(successful_symbols),
        "sina_fetch_failures": len(failed_symbols),
        "sina_fetch_coverage": coverage,
        "symbols_with_931152_intervals": len(intervals_by_symbol),
        "reconstructed_periods": len(reconstruction_rows),
        "reconstructed_sizes": {
            str(row["expected_period"]): int(row["reconstructed_size"])
            for row in reconstruction_rows
        },
        "eastmoney_failures": len(eastmoney_failures),
        "formal_manifest_qualified": False,
        "manifest_updated": False,
        "model_results_read": False,
        "holdout_opened": False,
        "review_note": (
            "These are secondary reconstructed candidate sets. Formal period qualification "
            "requires review of fetch coverage, set size/continuity, unexplained differences, "
            "and at least one independent dated set/change cross-check."
        ),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
