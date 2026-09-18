from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.bounded_retry import call_with_bounded_network_retry
from tech_sentiment.capital_input_data import (
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
)
from tech_sentiment.data_akshare import fetch_stock_history
from tech_sentiment.financing_materialization import materialize_financing_history


def _calendar(path: str | Path) -> pd.DatetimeIndex:
    frame = pd.read_csv(path)
    column = "date" if "date" in frame.columns else frame.columns[0]
    values = pd.DatetimeIndex(
        pd.to_datetime(frame[column], errors="raise")
    ).normalize().sort_values().unique()
    if not len(values):
        raise ValueError("shared trading calendar is empty")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail fast when latest requested trading-day public inputs are not published."
    )
    parser.add_argument("--calendar-csv", required=True)
    args = parser.parse_args()

    calendar = _calendar(args.calendar_csv)
    latest = pd.Timestamp(calendar.max()).normalize()

    etf = fetch_sse_etf_share_history(
        trading_dates=[latest],
        fund_codes=["588000"],
        sleep_seconds=0,
    )
    if len(etf.data) != 1 or len(etf.errors):
        raise SystemExit(
            "V4-A source freshness preflight failed: latest 588000 share data unavailable "
            f"for {latest.date()}; errors={etf.errors.to_dict('records')}"
        )

    turnover = fetch_sse_szse_a_share_turnover_history(
        trading_dates=[latest],
        sleep_seconds=0,
    )
    if len(turnover.combined) != 1 or len(turnover.errors):
        raise SystemExit(
            "V4-A source freshness preflight failed: latest bilateral turnover unavailable "
            f"for {latest.date()}; errors={turnover.errors.to_dict('records')}"
        )

    financing = materialize_financing_history([latest])
    if (
        len(financing.canonical) != 1
        or len(financing.errors)
        or float(financing.summary.get("bilateral_coverage") or 0.0) != 1.0
    ):
        raise SystemExit(
            "V4-A source freshness preflight failed: latest bilateral financing unavailable "
            f"for {latest.date()}; errors={financing.errors.to_dict('records')}"
        )

    price = pd.DataFrame()
    price_symbol = ""
    price_errors: list[str] = []
    for symbol in ("600519", "000001"):
        for provider in ("tencent", "eastmoney"):
            try:
                candidate = call_with_bounded_network_retry(
                    lambda s=symbol, p=provider: fetch_stock_history(
                        s,
                        start_date=str(latest.date()),
                        end_date=str(latest.date()),
                        adjust="",
                        provider=p,
                    ),
                    attempts=3,
                    backoff_seconds=0.5,
                )
                if candidate is not None and len(candidate):
                    price = candidate
                    price_symbol = symbol
                    break
                price_errors.append(f"{symbol}:{provider}:empty")
            except Exception as exc:
                price_errors.append(
                    f"{symbol}:{provider}:{type(exc).__name__}:{exc}"
                )
        if not price.empty:
            break
    if price.empty:
        raise SystemExit(
            "V4-A source freshness preflight failed: representative latest close unavailable "
            f"for {latest.date()}; errors={price_errors}"
        )

    print(
        json.dumps(
            {
                "status": "V4A_LATEST_SOURCE_FRESHNESS_OK",
                "latest_trading_date": str(latest.date()),
                "etf_588000_rows": int(len(etf.data)),
                "turnover_rows": int(len(turnover.combined)),
                "financing_rows": int(len(financing.canonical)),
                "representative_price_symbol": price_symbol,
                "representative_price_rows": int(len(price)),
                "diagnostic_only": True,
                "canonical_evidence_output": False,
                "forward_outcome_read": False,
                "parameter_search": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
