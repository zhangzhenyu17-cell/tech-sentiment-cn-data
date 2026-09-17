from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.sector_limit_coverage_strict import audit_strict_member_day_limit_coverage
from tech_sentiment.sector_limit_pipeline import build_and_audit_sector_limit_rows
from tech_sentiment.universe import apply_universe_membership


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
DESIGN_START = "2019-04-22"
DESIGN_END = "2023-12-31"
MIN_DAILY_COVERAGE = 0.95


def _rows(result) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    return rows


def _baostock_code(symbol: str) -> str:
    text = str(symbol).strip().zfill(6)
    if text.startswith(("6", "9")):
        return f"sh.{text}"
    return f"sz.{text}"


def _bool01(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Outcome-free 931152 design-period BaoStock limit-rule coverage probe."
    )
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        import baostock as bs
    except ImportError as exc:
        raise SystemExit("install the sector-data extra: pip install -e '.[sector-data]'") from exc

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.universe, dtype={"symbol": str})
    codes = [_baostock_code(symbol) for symbol in sorted(set(universe["symbol"]))]

    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed: {login.error_msg}")

    history_parts: list[pd.DataFrame] = []
    basic_parts: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    trade_calendar = pd.DataFrame()
    try:
        try:
            calendar_result = bs.query_trade_dates(
                start_date=DESIGN_START,
                end_date=DESIGN_END,
            )
            calendar_rows = _rows(calendar_result)
            if calendar_rows:
                trade_calendar = pd.DataFrame(calendar_rows, columns=calendar_result.fields)
            else:
                failures.append(
                    {"code": "MARKET", "stage": "trade_calendar", "error": "empty trade calendar"}
                )
        except Exception as exc:
            failures.append(
                {
                    "code": "MARKET",
                    "stage": "trade_calendar",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        for code in codes:
            try:
                result = bs.query_history_k_data_plus(
                    code,
                    FIELDS,
                    start_date=DESIGN_START,
                    end_date=DESIGN_END,
                    frequency="d",
                    adjustflag="3",
                )
                data = _rows(result)
                if data:
                    history_parts.append(pd.DataFrame(data, columns=result.fields))
                else:
                    failures.append({"code": code, "stage": "history", "error": "empty history"})
            except Exception as exc:
                failures.append(
                    {"code": code, "stage": "history", "error": f"{type(exc).__name__}: {exc}"}
                )

            try:
                basic = bs.query_stock_basic(code=code)
                basic_data = _rows(basic)
                if basic_data:
                    basic_parts.append(pd.DataFrame(basic_data, columns=basic.fields))
                else:
                    failures.append(
                        {"code": code, "stage": "stock_basic", "error": "empty stock_basic"}
                    )
            except Exception as exc:
                failures.append(
                    {
                        "code": code,
                        "stage": "stock_basic",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        bs.logout()

    history = pd.concat(history_parts, ignore_index=True) if history_parts else pd.DataFrame()
    stock_basic = pd.concat(basic_parts, ignore_index=True) if basic_parts else pd.DataFrame()
    history.to_csv(out / "baostock_history_status.csv", index=False)
    stock_basic.to_csv(out / "baostock_stock_basic.csv", index=False)
    trade_calendar.to_csv(out / "baostock_trade_calendar.csv", index=False)
    (out / "fetch_failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    required_calendar_columns = {"calendar_date", "is_trading_day"}
    calendar_usable = (
        not trade_calendar.empty
        and required_calendar_columns.issubset(set(trade_calendar.columns))
    )
    if history.empty or stock_basic.empty or not calendar_usable:
        report = {
            "status": "LIMIT_RULE_EVIDENCE_INSUFFICIENT",
            "reason": "BaoStock history, stock_basic, or independent trade calendar is unavailable",
            "provider": "BaoStock",
            "provider_version": version("baostock"),
            "codes_requested": len(codes),
            "fetch_failures": len(failures),
            "trade_calendar_rows": int(len(trade_calendar)),
            "holdout_opened": False,
            "model_outcomes_read": False,
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0

    market_trading = trade_calendar[
        trade_calendar["is_trading_day"].map(_bool01)
    ].copy()
    trading_dates = sorted(
        pd.to_datetime(market_trading["calendar_date"], errors="raise")
        .dt.normalize()
        .unique()
    )
    if not trading_dates:
        report = {
            "status": "LIMIT_RULE_EVIDENCE_INSUFFICIENT",
            "reason": "independent BaoStock trade calendar contains no trading days",
            "provider": "BaoStock",
            "provider_version": version("baostock"),
            "codes_requested": len(codes),
            "fetch_failures": len(failures),
            "trade_calendar_rows": int(len(trade_calendar)),
            "holdout_opened": False,
            "model_outcomes_read": False,
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0

    result = build_and_audit_sector_limit_rows(
        history,
        stock_basic,
        universe,
        min_daily_coverage=MIN_DAILY_COVERAGE,
    )
    enriched = result.rows.copy()
    enriched["symbol"] = enriched["code"].astype(str).str.replace(
        r"^(?:sh|sz)\.", "", regex=True
    )
    strict = audit_strict_member_day_limit_coverage(
        enriched,
        universe,
        trading_dates,
        min_daily_coverage=MIN_DAILY_COVERAGE,
    )

    active = apply_universe_membership(enriched, universe)
    active.to_csv(out / "active_limit_rows.csv", index=False)
    strict.daily_coverage.to_csv(out / "strict_daily_coverage.csv", index=False)
    result.coverage_audit.daily_coverage.to_csv(
        out / "row_conditional_daily_coverage.csv", index=False
    )

    unknown = int(active["special_day_status"].astype(str).eq("unknown").sum())
    no_limit = int(active["special_day_status"].astype(str).eq("no_limit").sum())
    suspended = int(active["tradestatus"].map(lambda value: not _bool01(value)).sum())
    st_rows = int(active["isST"].map(_bool01).sum())
    board_distribution = {
        str(key): int(value)
        for key, value in active["board"].astype(str).value_counts().sort_index().items()
    }
    reason_distribution = {
        str(key): int(value)
        for key, value in active["limit_rule_reason"].astype(str).value_counts().sort_index().items()
    }

    report = {
        "status": (
            "LIMIT_RULE_HISTORICAL_DESIGN_ELIGIBLE"
            if strict.eligible
            else "LIMIT_RULE_EVIDENCE_INSUFFICIENT"
        ),
        "qualification_scope": "historical_design_research_input_only",
        "provider": "BaoStock",
        "provider_version": version("baostock"),
        "query_fields": FIELDS.split(","),
        "adjustflag": "3",
        "trade_calendar_source": "BaoStock query_trade_dates",
        "design_start": DESIGN_START,
        "design_end": DESIGN_END,
        "codes_requested": len(codes),
        "fetch_failures": len(failures),
        "raw_history_rows": int(len(history)),
        "stock_basic_rows": int(len(stock_basic)),
        "trade_calendar_rows": int(len(trade_calendar)),
        "market_trading_days": len(trading_dates),
        "active_rows_returned": int(len(active)),
        "expected_member_days": strict.expected_member_days,
        "observed_member_days": strict.observed_member_days,
        "eligible_member_days": strict.eligible_member_days,
        "missing_member_days": strict.missing_member_days,
        "minimum_daily_observation_coverage": strict.minimum_daily_observation_coverage,
        "minimum_daily_limit_coverage": strict.minimum_daily_coverage,
        "required_daily_limit_coverage": strict.required_daily_coverage,
        "days_below_threshold": strict.days_below_threshold,
        "strict_errors": list(strict.errors),
        "unknown_special_day_rows": unknown,
        "known_no_limit_rows": no_limit,
        "suspended_or_nontrading_rows": suspended,
        "st_rows": st_rows,
        "board_distribution": board_distribution,
        "limit_rule_reason_distribution": reason_distribution,
        "row_conditional_gate_eligible": result.coverage_audit.eligible,
        "row_conditional_minimum_coverage": result.coverage_audit.minimum_daily_coverage,
        "holdout_opened": False,
        "model_outcomes_read": False,
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
