from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.capital_input_data import qualify_trailing_etf_coverage
from tech_sentiment.resumable_capital import materialize_capital_monthly
from tech_sentiment.v4c03_szse_etf_shares import fetch_szse_etf_share_history


STAR_FUND = "588000"
CHINEXT_FUND = "159915"


def _read_calendar(path: Path) -> pd.DatetimeIndex:
    frame = pd.read_csv(path)
    if "date" not in frame.columns:
        raise ValueError("calendar CSV missing date")
    dates = (
        pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(dates):
        raise ValueError("calendar is empty")
    return dates


def _coverage(history: pd.DataFrame, dates: pd.DatetimeIndex, code: str) -> pd.DataFrame:
    out = qualify_trailing_etf_coverage(
        history,
        trading_dates=dates,
        fund_code=code,
        window=60,
        min_coverage=0.80,
    )
    shares = pd.to_numeric(out["fund_shares"], errors="coerce")
    out["endpoint_20d_available"] = shares.notna() & shares.shift(20).notna()
    out["endpoint_60d_available"] = shares.notna() & shares.shift(60).notna()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize public-only V4C-03 Phase A capital rails."
    )
    parser.add_argument("--calendar-csv", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    dates = _read_calendar(args.calendar_csv)
    start = str(pd.Timestamp(dates.min()).date())
    end = str(pd.Timestamp(dates.max()).date())

    sse = materialize_capital_monthly(
        trading_dates=dates,
        fund_codes=[STAR_FUND],
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir / "sse",
        sleep_seconds=0.05,
    )
    szse = fetch_szse_etf_share_history(
        start_date=start,
        end_date=end,
        trading_dates=dates,
        fund_codes=[CHINEXT_FUND],
        sleep_seconds=0.05,
    )

    star = sse.etf.data.copy()
    chi = szse.data.copy()
    shares = pd.concat([star, chi], ignore_index=True, sort=False)
    if len(shares):
        shares["date"] = pd.to_datetime(shares["date"], errors="raise").dt.normalize()
        shares["fund_code"] = shares["fund_code"].astype(str).str.zfill(6)
        if shares.duplicated(["date", "fund_code"]).any():
            raise ValueError("capital ETF shares contain duplicate date/fund rows")
        shares = shares.sort_values(["date", "fund_code"]).reset_index(drop=True)

    star_cov = _coverage(shares, dates, STAR_FUND)
    chi_cov = _coverage(shares, dates, CHINEXT_FUND)
    coverage = pd.concat([star_cov, chi_cov], ignore_index=True, sort=False)

    turnover = sse.turnover.combined.copy()
    if len(turnover):
        turnover["date"] = pd.to_datetime(turnover["date"], errors="raise").dt.normalize()
        if turnover["date"].duplicated().any():
            raise ValueError("broad-market turnover contains duplicate dates")
    turnover_complete = int(len(turnover))
    turnover_errors = sse.turnover.errors.copy()

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": dates}).to_csv(
        out / "trading_calendar.csv", index=False, date_format="%Y-%m-%d"
    )
    shares.to_csv(out / "etf_shares.csv", index=False, date_format="%Y-%m-%d")
    coverage.to_csv(out / "etf_share_coverage.csv", index=False, date_format="%Y-%m-%d")
    sse.etf.errors.to_csv(out / "sse_etf_share_errors.csv", index=False)
    szse.errors.to_csv(out / "szse_etf_share_errors.csv", index=False)
    sse.turnover.sse.to_csv(out / "sse_a_share_turnover.csv", index=False)
    sse.turnover.szse.to_csv(out / "szse_a_share_turnover.csv", index=False)
    turnover.to_csv(out / "sse_szse_a_share_turnover.csv", index=False)
    turnover_errors.to_csv(out / "sse_szse_turnover_errors.csv", index=False)

    by_fund = {}
    for code in (STAR_FUND, CHINEXT_FUND):
        x = coverage[coverage["fund_code"].astype(str).eq(code)].copy()
        by_fund[code] = {
            "observed_days": int(x["observed"].fillna(False).sum()),
            "raw_coverage": float(x["observed"].mean()) if len(x) else 0.0,
            "eligible_days": int(x["eligible"].fillna(False).sum()),
            "endpoint_20d_days": int(x["endpoint_20d_available"].fillna(False).sum()),
            "endpoint_60d_days": int(x["endpoint_60d_available"].fillna(False).sum()),
        }

    summary = {
        "schema_version": "v4c03-phase-a-capital-v1",
        "status": "V4C03_PUBLIC_CAPITAL_MATERIALIZED",
        "start_date": start,
        "end_date": end,
        "source_commit": args.source_commit,
        "funds": by_fund,
        "turnover_complete_days": turnover_complete,
        "turnover_expected_days": int(len(dates)),
        "turnover_error_rows": int(len(turnover_errors)),
        "sse_etf_error_rows": int(len(sse.etf.errors)),
        "szse_etf_error_chunks": int(len(szse.errors)),
        "source_identities": {
            STAR_FUND: "SSE_ETF_SCALE_DAILY",
            CHINEXT_FUND: "SZSE_ETF_SCALE_DAILY",
            "turnover": "SSE_DAILY_STOCK_OVERVIEW+SZSE_MARKET_OVERVIEW_DAILY",
        },
        "no_interpolation": True,
        "no_forward_fill": True,
        "future_prices_or_returns_used": False,
        "predictive_research_run": False,
        "parameter_search_run": False,
        "production_or_trading_authority_changed": False,
    }
    (out / "capital_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
