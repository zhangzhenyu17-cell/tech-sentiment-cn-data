from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.csindex_index_price import fetch_csindex_history
from tech_sentiment.data_akshare import download_universe_history
from tech_sentiment.eastmoney_fund_holdings_evidence import (
    TRACKING_ETF_931152,
    fetch_and_parse_holdings_year,
)
from tech_sentiment.sector_limit_coverage_strict import audit_strict_member_day_limit_coverage
from tech_sentiment.sector_limit_pipeline import build_and_audit_sector_limit_rows
from tech_sentiment.sina_index_membership_evidence import fetch_membership_intervals
from tech_sentiment.universe import apply_universe_membership

INDEX_CODE = "931152"
HOLDOUT_START = pd.Timestamp("2024-01-01")
HOLDOUT_END = pd.Timestamp("2026-09-11")
STOCK_WARMUP_START = pd.Timestamp("2022-01-01")
STOCK_ADJUSTMENT = "qfq"
MIN_DAILY_COVERAGE = 0.95
BAOSTOCK_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
CROSSCHECK_DATES = (
    "2023-12-31",
    "2024-06-30",
    "2024-12-31",
    "2025-06-30",
    "2025-12-31",
    "2026-06-30",
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _bool01(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _baostock_code(symbol: str) -> str:
    text = str(symbol).strip().zfill(6)
    return f"sh.{text}" if text.startswith(("6", "9")) else f"sz.{text}"


def _baostock_rows(result) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    return rows


def _active_design_anchor(design_universe: pd.DataFrame) -> set[str]:
    frame = design_universe.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame["effective_start"] = pd.to_datetime(frame["effective_start"], errors="raise").dt.normalize()
    frame["effective_end"] = pd.to_datetime(frame["effective_end"], errors="raise").dt.normalize()
    day = pd.Timestamp("2023-12-31")
    mask = (frame["effective_start"] <= day) & (frame["effective_end"] >= day)
    return set(frame.loc[mask, "symbol"])


def _clip_intervals(intervals_by_symbol: dict[str, list[object]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    exclusive_end = HOLDOUT_END + pd.Timedelta(days=1)
    for symbol, intervals in intervals_by_symbol.items():
        for item in intervals:
            start = pd.Timestamp(item.start_date).normalize()
            end_exclusive = pd.Timestamp(item.end_date).normalize() if item.end_date else exclusive_end
            clipped_start = max(start, HOLDOUT_START)
            clipped_end_exclusive = min(end_exclusive, exclusive_end)
            if clipped_start >= clipped_end_exclusive:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "effective_start": clipped_start,
                    "effective_end": clipped_end_exclusive - pd.Timedelta(days=1),
                    "source_index": INDEX_CODE,
                    "universe_mode": "point_in_time",
                    "qualification_scope": "frozen_holdout_research_input_only",
                    "source_url": item.source_url,
                    "response_sha256": item.response_sha256,
                    "source_interval_start": item.start_date,
                    "source_interval_end_exclusive": item.end_date,
                }
            )
    if not rows:
        raise ValueError("no 931152 holdout membership intervals reconstructed")
    out = pd.DataFrame(rows).sort_values(["symbol", "effective_start"]).reset_index(drop=True)
    if out.duplicated(["symbol", "effective_start", "effective_end"]).any():
        raise ValueError("duplicate clipped membership intervals")
    return out


def _members_on(universe: pd.DataFrame, value: str | pd.Timestamp) -> set[str]:
    day = pd.Timestamp(value).normalize()
    return set(
        universe.loc[
            (universe["effective_start"] <= day) & (universe["effective_end"] >= day),
            "symbol",
        ].astype(str)
    )


def _fetch_baostock_limit_inputs(universe: pd.DataFrame, out: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    try:
        import baostock as bs
        from importlib.metadata import version
    except ImportError as exc:
        raise RuntimeError("install sector-data extra") from exc

    codes = [_baostock_code(symbol) for symbol in sorted(set(universe["symbol"].astype(str)))]
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")

    history_parts: list[pd.DataFrame] = []
    basic_parts: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    trade_calendar = pd.DataFrame()
    try:
        calendar_result = bs.query_trade_dates(
            start_date=HOLDOUT_START.strftime("%Y-%m-%d"),
            end_date=HOLDOUT_END.strftime("%Y-%m-%d"),
        )
        calendar_rows = _baostock_rows(calendar_result)
        if calendar_rows:
            trade_calendar = pd.DataFrame(calendar_rows, columns=calendar_result.fields)
        for code in codes:
            try:
                result = bs.query_history_k_data_plus(
                    code,
                    BAOSTOCK_FIELDS,
                    start_date=HOLDOUT_START.strftime("%Y-%m-%d"),
                    end_date=HOLDOUT_END.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="3",
                )
                rows = _baostock_rows(result)
                if rows:
                    history_parts.append(pd.DataFrame(rows, columns=result.fields))
                else:
                    failures.append({"code": code, "stage": "history", "error": "empty history"})
            except Exception as exc:
                failures.append({"code": code, "stage": "history", "error": f"{type(exc).__name__}: {exc}"})
            try:
                basic = bs.query_stock_basic(code=code)
                rows = _baostock_rows(basic)
                if rows:
                    basic_parts.append(pd.DataFrame(rows, columns=basic.fields))
                else:
                    failures.append({"code": code, "stage": "stock_basic", "error": "empty stock_basic"})
            except Exception as exc:
                failures.append({"code": code, "stage": "stock_basic", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        bs.logout()

    history = pd.concat(history_parts, ignore_index=True) if history_parts else pd.DataFrame()
    stock_basic = pd.concat(basic_parts, ignore_index=True) if basic_parts else pd.DataFrame()
    history.to_csv(out / "baostock_history_status.csv", index=False)
    stock_basic.to_csv(out / "baostock_stock_basic.csv", index=False)
    trade_calendar.to_csv(out / "baostock_trade_calendar.csv", index=False)
    _dump_jsonl(out / "baostock_fetch_failures.jsonl", failures)
    if history.empty or stock_basic.empty or trade_calendar.empty:
        raise ValueError("BaoStock holdout limit inputs are incomplete")

    market_trading = trade_calendar[trade_calendar["is_trading_day"].map(_bool01)].copy()
    trading_dates = sorted(pd.to_datetime(market_trading["calendar_date"], errors="raise").dt.normalize().unique())
    if not trading_dates:
        raise ValueError("BaoStock holdout trading calendar is empty")

    result = build_and_audit_sector_limit_rows(
        history,
        stock_basic,
        universe,
        min_daily_coverage=MIN_DAILY_COVERAGE,
    )
    enriched = result.rows.copy()
    enriched["symbol"] = enriched["code"].astype(str).str.replace(r"^(?:sh|sz)\.", "", regex=True)
    strict = audit_strict_member_day_limit_coverage(
        enriched,
        universe,
        trading_dates,
        min_daily_coverage=MIN_DAILY_COVERAGE,
    )
    active = apply_universe_membership(enriched, universe)
    active.to_csv(out / "active_limit_rows.csv", index=False)
    strict.daily_coverage.to_csv(out / "strict_daily_coverage.csv", index=False)
    if not strict.eligible:
        raise ValueError("strict holdout limit-rule coverage failed: " + "; ".join(strict.errors))

    report = {
        "provider": "BaoStock",
        "provider_version": version("baostock"),
        "codes_requested": len(codes),
        "fetch_failures": len(failures),
        "market_trading_days": len(trading_dates),
        "expected_member_days": strict.expected_member_days,
        "observed_member_days": strict.observed_member_days,
        "eligible_member_days": strict.eligible_member_days,
        "missing_member_days": strict.missing_member_days,
        "minimum_daily_observation_coverage": strict.minimum_daily_observation_coverage,
        "minimum_daily_limit_coverage": strict.minimum_daily_coverage,
        "required_daily_limit_coverage": strict.required_daily_coverage,
        "days_below_threshold": strict.days_below_threshold,
        "strict_errors": list(strict.errors),
    }
    return active, strict.daily_coverage, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-free 931152 frozen 2024+ holdout input builder.")
    parser.add_argument("--design-universe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.workers <= 12:
        raise ValueError("workers must be in [1, 12]")

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    design_universe = pd.read_csv(args.design_universe, dtype={"symbol": str})
    anchor = _active_design_anchor(design_universe)
    if len(anchor) != 50:
        raise ValueError(f"2023-12-31 frozen design anchor must contain 50 members, got {len(anchor)}")

    batches = []
    raw_holdings: list[dict[str, object]] = []
    holdings_failures: list[dict[str, object]] = []
    for year in range(2023, 2027):
        try:
            year_batches, years, raw_text = fetch_and_parse_holdings_year(
                TRACKING_ETF_931152, year, topline=100, timeout=args.timeout
            )
            batches.extend(year_batches)
            (out / f"eastmoney_159992_{year}.txt").write_text(raw_text, encoding="utf-8")
            raw_holdings.append({"year": year, "advertised_years": list(years), "status": "ok"})
        except Exception as exc:
            holdings_failures.append({"year": year, "error": f"{type(exc).__name__}: {exc}"})
    _dump_jsonl(out / "eastmoney_fetch_audit.jsonl", raw_holdings + holdings_failures)
    full_batches = {batch.report_date: batch for batch in batches if batch.full_report_candidate_set}
    required_reports = set(CROSSCHECK_DATES)
    missing_reports = sorted(required_reports - set(full_batches))
    if missing_reports:
        raise ValueError(f"missing full-report ETF candidate sets: {missing_reports}")

    candidate_union = set(anchor)
    for batch in full_batches.values():
        candidate_union.update(batch.symbols)

    intervals_by_symbol: dict[str, list[object]] = {}
    fetch_audit: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_membership_intervals, symbol, timeout=args.timeout, index_code=INDEX_CODE): symbol
            for symbol in sorted(candidate_union)
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                intervals, html = future.result()
                (out / f"sina_{symbol}.html").write_text(html, encoding="utf-8")
                intervals_by_symbol[symbol] = intervals
                fetch_audit.append({
                    "symbol": symbol,
                    "status": "ok",
                    "interval_count": len(intervals),
                    "response_sha256": intervals[0].response_sha256 if intervals else None,
                })
            except Exception as exc:
                fetch_audit.append({"symbol": symbol, "status": "failed_closed", "error": f"{type(exc).__name__}: {exc}"})
    _dump_jsonl(out / "sina_fetch_audit.jsonl", sorted(fetch_audit, key=lambda row: str(row["symbol"])))
    failures = [row for row in fetch_audit if row["status"] != "ok"]
    if failures:
        raise ValueError(f"Sina membership fetch failures: {len(failures)}")

    universe = _clip_intervals(intervals_by_symbol)
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    start_members = _members_on(universe, HOLDOUT_START)
    if start_members != anchor:
        raise ValueError(
            f"holdout membership does not continue frozen 2023-12-31 anchor: "
            f"missing={sorted(anchor-start_members)}, extra={sorted(start_members-anchor)}"
        )

    crosschecks: list[dict[str, object]] = []
    for report_date in CROSSCHECK_DATES:
        reconstructed = _members_on(universe, report_date)
        candidates = set(full_batches[report_date].symbols)
        missing_from_report = sorted(reconstructed - candidates)
        crosschecks.append({
            "date": report_date,
            "reconstructed_size": len(reconstructed),
            "candidate_report_size": len(candidates),
            "missing_from_same_report": missing_from_report,
            "all_reconstructed_in_same_report": not missing_from_report,
            "candidate_response_sha256": full_batches[report_date].response_sha256,
            "candidate_source_url": full_batches[report_date].source_url,
        })
    _dump_jsonl(out / "membership_crosschecks.jsonl", crosschecks)
    if any(not row["all_reconstructed_in_same_report"] for row in crosschecks):
        raise ValueError("holdout membership failed same-report independent crosscheck")

    universe.to_csv(out / "holdout_universe.csv", index=False, date_format="%Y-%m-%d")
    all_intervals = [asdict(item) for intervals in intervals_by_symbol.values() for item in intervals]
    _dump_jsonl(out / "sina_931152_intervals.jsonl", all_intervals)

    download = download_universe_history(
        universe,
        start_date=STOCK_WARMUP_START.strftime("%Y-%m-%d"),
        end_date=HOLDOUT_END.strftime("%Y-%m-%d"),
        adjust=STOCK_ADJUSTMENT,
        providers=("eastmoney", "tencent"),
        retries=1,
        retry_backoff_seconds=0.75,
        sleep_seconds=0.0,
        fail_fast=False,
    )
    prices = download.prices.copy()
    errors = download.errors.copy()
    prices.to_csv(out / "stock_prices_qfq.csv", index=False, date_format="%Y-%m-%d")
    errors.to_csv(out / "stock_download_errors.csv", index=False)
    missing_price_symbols = sorted(set(universe["symbol"]) - set(prices["symbol"].astype(str).str.zfill(6)))
    if missing_price_symbols:
        raise ValueError(f"missing stock histories: {missing_price_symbols}")

    index_prices = fetch_csindex_history(
        INDEX_CODE,
        start_date=HOLDOUT_START.strftime("%Y-%m-%d"),
        end_date=HOLDOUT_END.strftime("%Y-%m-%d"),
        retries=2,
        retry_backoff_seconds=0.75,
        timeout_seconds=20.0,
    )
    index_prices.to_csv(out / "index_931152_prices.csv", index=False, date_format="%Y-%m-%d")
    if index_prices.empty:
        raise ValueError("official 931152 holdout index rail is empty")
    codes = set(index_prices["index_code"].astype(str).str.zfill(6))
    if codes != {INDEX_CODE}:
        raise ValueError(f"unexpected official index codes: {sorted(codes)}")
    max_date = pd.to_datetime(index_prices["date"], errors="raise").dt.normalize().max()
    if max_date > HOLDOUT_END:
        raise ValueError("official index rail crosses frozen holdout end")

    _, strict_daily, limit_report = _fetch_baostock_limit_inputs(universe, out)

    membership_counts = []
    for value in pd.to_datetime(index_prices["date"], errors="raise").dt.normalize():
        membership_counts.append(len(_members_on(universe, value)))
    if not membership_counts or min(membership_counts) <= 0:
        raise ValueError("holdout universe has empty membership on official trading dates")

    file_names = [
        "holdout_universe.csv",
        "stock_prices_qfq.csv",
        "stock_download_errors.csv",
        "index_931152_prices.csv",
        "active_limit_rows.csv",
        "strict_daily_coverage.csv",
        "membership_crosschecks.jsonl",
        "sina_931152_intervals.jsonl",
        "sina_fetch_audit.jsonl",
        "eastmoney_fetch_audit.jsonl",
    ]
    files = {name: _sha256_file(out / name) for name in file_names}
    report = {
        "status": "HOLDOUT_INPUT_QUALIFIED",
        "qualification_scope": "outcome_free_frozen_holdout_input_only",
        "index_code": INDEX_CODE,
        "tracking_fund": TRACKING_ETF_931152,
        "holdout_start": HOLDOUT_START.strftime("%Y-%m-%d"),
        "holdout_end": HOLDOUT_END.strftime("%Y-%m-%d"),
        "stock_warmup_start": STOCK_WARMUP_START.strftime("%Y-%m-%d"),
        "stock_adjustment": STOCK_ADJUSTMENT,
        "anchor_members": len(anchor),
        "candidate_union_size": len(candidate_union),
        "holdout_universe_symbols": int(universe["symbol"].nunique()),
        "holdout_universe_intervals": int(len(universe)),
        "minimum_member_count_on_official_trading_dates": min(membership_counts),
        "maximum_member_count_on_official_trading_dates": max(membership_counts),
        "membership_crosschecks": crosschecks,
        "stock_rows": int(len(prices)),
        "stock_symbols": int(prices["symbol"].astype(str).nunique()),
        "stock_download_error_rows": int(len(errors)),
        "stock_provider_distribution": {
            str(k): int(v) for k, v in prices["provider"].astype(str).value_counts().sort_index().items()
        },
        "index_rows": int(len(index_prices)),
        "index_provider_distribution": {
            str(k): int(v) for k, v in index_prices["provider"].astype(str).value_counts().sort_index().items()
        },
        "limit_rule": limit_report,
        "strict_coverage_rows": int(len(strict_daily)),
        "files": files,
        "model_events_computed": False,
        "forward_outcomes_computed": False,
        "holdout_model_evaluation_opened": False,
        "raw_holdout_inputs_packaged": True,
    }
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
