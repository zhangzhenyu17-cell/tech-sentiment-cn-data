from __future__ import annotations

import argparse
from datetime import datetime
import json
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
    combine_sse_szse_a_share_turnover,
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
    qualify_trailing_etf_coverage,
)
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.resumable_checkpoint import (
    load_csv_checkpoint,
    merge_csv_checkpoint,
)


ETF_COLUMNS = [
    "date",
    "fund_code",
    "fund_shares",
    "unit",
    "source_identity",
    "source_url",
    "provider_interface",
    "evidence_available_date",
]
SSE_TURNOVER_COLUMNS = [
    "date",
    "sse_a_share_turnover_yuan",
    "source_identity",
    "source_url",
    "source_unit",
]
SZSE_TURNOVER_COLUMNS = [
    "date",
    "szse_a_share_turnover_yuan",
    "source_identity",
    "source_url",
    "source_unit",
]


def _etf_readiness(coverage: pd.DataFrame) -> str:
    if coverage.empty or not bool(coverage["observed"].any()):
        return "DATA_INSUFFICIENT"
    mature = coverage.iloc[60:].copy() if len(coverage) > 60 else coverage.iloc[0:0].copy()
    if mature.empty:
        return "PARTIAL_COVERAGE"
    required = (
        mature["eligible"].fillna(False)
        & mature["endpoint_20d_available"].fillna(False)
        & mature["endpoint_60d_available"].fillna(False)
    )
    if bool(required.all()) and bool(coverage.iloc[0]["observed"]):
        return "QUALIFIED_INPUT"
    return "PARTIAL_COVERAGE"


def _chunks(values: list[pd.Timestamp], size: int) -> list[list[pd.Timestamp]]:
    if size <= 0:
        raise ValueError("checkpoint batch size must be > 0")
    return [values[index : index + size] for index in range(0, len(values), size)]


def _date_set(frame: pd.DataFrame) -> set[str]:
    if frame.empty:
        return set()
    return set(pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d"))


def _filter_dates(frame: pd.DataFrame, wanted: set[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    dates = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    return frame.loc[dates.isin(wanted)].copy().reset_index(drop=True)


def _concat_errors(parts: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
    usable = [part for part in parts if part is not None and not part.empty]
    if not usable:
        return pd.DataFrame(columns=columns)
    return pd.concat(usable, ignore_index=True, sort=False)[columns]


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual-only public capital-input qualification diagnostic.")
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--fund-code", default="588000")
    parser.add_argument("--calendar-index-code", default="000906")
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--checkpoint-batch-size", type=int, default=20)
    parser.add_argument("--out-dir", default="output/capital_input_qualification")
    args = parser.parse_args()

    if args.end_date is None:
        args.end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if args.checkpoint_batch_size <= 0:
        raise SystemExit("checkpoint batch size must be > 0")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calendar = fetch_index_history(
        args.calendar_index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if calendar.empty:
        raise SystemExit("trading calendar source returned no rows")
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["date"], errors="raise")).normalize().sort_values().unique()
    wanted_dates = {pd.Timestamp(value).strftime("%Y-%m-%d") for value in dates}
    fund_code = str(args.fund_code).zfill(6)

    checkpoint_root = Path(args.checkpoint_dir) if args.checkpoint_dir else None
    if checkpoint_root is not None:
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        etf_path = checkpoint_root / "sse_etf_shares_success.csv"
        sse_turnover_path = checkpoint_root / "sse_turnover_success.csv"
        szse_turnover_path = checkpoint_root / "szse_turnover_success.csv"
        etf_all = load_csv_checkpoint(
            etf_path,
            required_columns=ETF_COLUMNS,
            key_columns=["date", "fund_code"],
            expected_constants={
                "source_identity": SSE_ETF_SHARE_SOURCE_ID,
                "source_url": SSE_ETF_SHARE_SOURCE_URL,
                "unit": "share",
                "provider_interface": "akshare.fund_etf_scale_sse",
            },
        )
        sse_all = load_csv_checkpoint(
            sse_turnover_path,
            required_columns=SSE_TURNOVER_COLUMNS,
            key_columns=["date"],
            expected_constants={
                "source_identity": SSE_TURNOVER_SOURCE_ID,
                "source_url": SSE_TURNOVER_SOURCE_URL,
                "source_unit": "100_million_yuan",
            },
        )
        szse_all = load_csv_checkpoint(
            szse_turnover_path,
            required_columns=SZSE_TURNOVER_COLUMNS,
            key_columns=["date"],
            expected_constants={
                "source_identity": SZSE_TURNOVER_SOURCE_ID,
                "source_url": SZSE_TURNOVER_SOURCE_URL,
                "source_unit": "yuan",
            },
        )
    else:
        etf_path = sse_turnover_path = szse_turnover_path = None
        etf_all = pd.DataFrame(columns=ETF_COLUMNS)
        sse_all = pd.DataFrame(columns=SSE_TURNOVER_COLUMNS)
        szse_all = pd.DataFrame(columns=SZSE_TURNOVER_COLUMNS)

    etf_seed = _filter_dates(etf_all, wanted_dates)
    if len(etf_seed):
        etf_seed = etf_seed.loc[etf_seed["fund_code"].astype(str).str.zfill(6).eq(fund_code)].copy()
    sse_seed = _filter_dates(sse_all, wanted_dates)
    szse_seed = _filter_dates(szse_all, wanted_dates)
    etf_reused = int(len(etf_seed))
    sse_turnover_reused = int(len(sse_seed))
    szse_turnover_reused = int(len(szse_seed))

    etf_known = _date_set(etf_seed)
    etf_pending = [pd.Timestamp(value) for value in dates if pd.Timestamp(value).strftime("%Y-%m-%d") not in etf_known]
    etf_error_parts: list[pd.DataFrame] = []
    for batch in _chunks(etf_pending, args.checkpoint_batch_size):
        result = fetch_sse_etf_share_history(
            trading_dates=batch,
            fund_codes=[fund_code],
            sleep_seconds=args.sleep_seconds,
        )
        if len(result.data):
            if etf_path is not None:
                etf_all = merge_csv_checkpoint(
                    etf_path,
                    result.data,
                    required_columns=ETF_COLUMNS,
                    key_columns=["date", "fund_code"],
                    expected_constants={
                        "source_identity": SSE_ETF_SHARE_SOURCE_ID,
                        "source_url": SSE_ETF_SHARE_SOURCE_URL,
                        "unit": "share",
                        "provider_interface": "akshare.fund_etf_scale_sse",
                    },
                )
            else:
                etf_all = pd.concat([etf_all, result.data], ignore_index=True, sort=False)
        if len(result.errors):
            etf_error_parts.append(result.errors)

    etf_data = _filter_dates(etf_all, wanted_dates)
    if len(etf_data):
        etf_data = etf_data.loc[etf_data["fund_code"].astype(str).str.zfill(6).eq(fund_code)].copy()
    etf_errors = _concat_errors(etf_error_parts, ["date", "error"])
    coverage = qualify_trailing_etf_coverage(
        etf_data,
        trading_dates=dates,
        fund_code=fund_code,
        window=60,
        min_coverage=0.80,
    )
    shares = pd.to_numeric(coverage["fund_shares"], errors="coerce")
    coverage["endpoint_20d_available"] = shares.notna() & shares.shift(20).notna()
    coverage["endpoint_60d_available"] = shares.notna() & shares.shift(60).notna()

    initial_sse_missing = wanted_dates - _date_set(sse_seed)
    initial_szse_missing = wanted_dates - _date_set(szse_seed)
    turnover_pending_dates = sorted(initial_sse_missing | initial_szse_missing)
    turnover_error_parts: list[pd.DataFrame] = []
    for batch_strings in [turnover_pending_dates[index : index + args.checkpoint_batch_size] for index in range(0, len(turnover_pending_dates), args.checkpoint_batch_size)]:
        batch = [pd.Timestamp(value) for value in batch_strings]
        result = fetch_sse_szse_a_share_turnover_history(
            trading_dates=batch,
            sleep_seconds=args.sleep_seconds,
        )
        if len(result.sse):
            new_sse_dates = pd.to_datetime(result.sse["date"], errors="raise").dt.strftime("%Y-%m-%d")
            new_sse = result.sse.loc[new_sse_dates.isin(initial_sse_missing)].copy()
            if len(new_sse):
                if sse_turnover_path is not None:
                    sse_all = merge_csv_checkpoint(
                        sse_turnover_path,
                        new_sse,
                        required_columns=SSE_TURNOVER_COLUMNS,
                        key_columns=["date"],
                        expected_constants={
                            "source_identity": SSE_TURNOVER_SOURCE_ID,
                            "source_url": SSE_TURNOVER_SOURCE_URL,
                            "source_unit": "100_million_yuan",
                        },
                    )
                else:
                    sse_all = pd.concat([sse_all, new_sse], ignore_index=True, sort=False)
        if len(result.szse):
            new_szse_dates = pd.to_datetime(result.szse["date"], errors="raise").dt.strftime("%Y-%m-%d")
            new_szse = result.szse.loc[new_szse_dates.isin(initial_szse_missing)].copy()
            if len(new_szse):
                if szse_turnover_path is not None:
                    szse_all = merge_csv_checkpoint(
                        szse_turnover_path,
                        new_szse,
                        required_columns=SZSE_TURNOVER_COLUMNS,
                        key_columns=["date"],
                        expected_constants={
                            "source_identity": SZSE_TURNOVER_SOURCE_ID,
                            "source_url": SZSE_TURNOVER_SOURCE_URL,
                            "source_unit": "yuan",
                        },
                    )
                else:
                    szse_all = pd.concat([szse_all, new_szse], ignore_index=True, sort=False)
        if len(result.errors):
            error_dates = result.errors["date"].astype(str)
            relevant = (
                (result.errors["exchange"].astype(str).eq("SSE") & error_dates.isin(initial_sse_missing))
                | (result.errors["exchange"].astype(str).eq("SZSE") & error_dates.isin(initial_szse_missing))
            )
            if relevant.any():
                turnover_error_parts.append(result.errors.loc[relevant].copy())

    sse_turnover = _filter_dates(sse_all, wanted_dates)
    szse_turnover = _filter_dates(szse_all, wanted_dates)
    turnover_combined = (
        combine_sse_szse_a_share_turnover(sse_turnover, szse_turnover)
        if len(sse_turnover) and len(szse_turnover)
        else pd.DataFrame(
            columns=[
                "date",
                "sse_a_share_turnover_yuan",
                "szse_a_share_turnover_yuan",
                "amount",
                "scope",
                "canonical_all_a_state",
            ]
        )
    )
    turnover_errors = _concat_errors(turnover_error_parts, ["date", "exchange", "error"])

    calendar.to_csv(out / "trading_calendar.csv", index=False)
    etf_data.to_csv(out / "sse_etf_shares.csv", index=False)
    etf_errors.to_csv(out / "sse_etf_share_errors.csv", index=False)
    coverage.to_csv(out / "sse_etf_share_coverage.csv", index=False)
    sse_turnover.to_csv(out / "sse_a_share_turnover.csv", index=False)
    szse_turnover.to_csv(out / "szse_a_share_turnover.csv", index=False)
    turnover_combined.to_csv(out / "sse_szse_a_share_turnover.csv", index=False)
    turnover_errors.to_csv(out / "sse_szse_turnover_errors.csv", index=False)

    qualified = coverage[coverage["coverage"].notna()]
    etf_state = _etf_readiness(coverage)
    turnover_complete_pct = float(len(turnover_combined) / len(dates)) if len(dates) else None
    turnover_state = (
        "QUALIFIED_INPUT"
        if len(dates) and len(turnover_combined) == len(dates) and turnover_errors.empty
        else "PARTIAL_COVERAGE" if len(turnover_combined) else "DATA_INSUFFICIENT"
    )
    summary = {
        "status": "MANUAL_DIAGNOSTIC_ONLY",
        "start_date": str(dates.min().date()),
        "end_date": str(dates.max().date()),
        "trading_days": int(len(dates)),
        "fund_code": fund_code,
        "etf_readiness_state": etf_state,
        "etf_observed_days": int(coverage["observed"].sum()),
        "etf_raw_coverage": float(coverage["observed"].mean()) if len(coverage) else None,
        "etf_trailing60_latest": float(qualified.iloc[-1]["coverage"]) if len(qualified) else None,
        "etf_trailing60_eligible_day_pct": float(qualified["eligible"].mean()) if len(qualified) else None,
        "etf_endpoint_20d_days": int(coverage["endpoint_20d_available"].sum()),
        "etf_endpoint_60d_days": int(coverage["endpoint_60d_available"].sum()),
        "etf_error_days": int(etf_errors["date"].nunique()) if len(etf_errors) else 0,
        "turnover_readiness_state": turnover_state,
        "sse_szse_turnover_complete_days": int(len(turnover_combined)),
        "sse_szse_turnover_complete_pct": turnover_complete_pct,
        "turnover_scope": "SSE_SZSE_A_SHARES",
        "canonical_all_a_state": "INCOMPLETE_BSE_NOT_INCLUDED",
        "resumable_checkpoint_enabled": checkpoint_root is not None,
        "checkpoint_batch_size": int(args.checkpoint_batch_size),
        "checkpoint_reused_etf_days": etf_reused,
        "checkpoint_reused_sse_turnover_days": sse_turnover_reused,
        "checkpoint_reused_szse_turnover_days": szse_turnover_reused,
        "production_or_model_output": False,
    }
    (out / "qualification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
