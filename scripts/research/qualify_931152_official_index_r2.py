from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.csindex_index_price import (
    CSINDEX_INDEX_PERF_URL,
    fetch_csindex_history,
)
from tech_sentiment.sector_design_input_contract import (
    DESIGN_END,
    DESIGN_START,
    INNOVATION_DRUG_INDEX,
    audit_design_price_inputs,
)


OVERLAP_CLOSE_ATOL = 0.011


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free r2 qualification for the official CSI 931152 design index rail. "
            "It does not compute model events or forward outcomes."
        )
    )
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--stock-prices", type=Path, required=True)
    parser.add_argument("--stock-errors", type=Path, required=True)
    parser.add_argument("--stock-report", type=Path, required=True)
    parser.add_argument("--strict-daily-coverage", type=Path, required=True)
    parser.add_argument("--eastmoney-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    universe = pd.read_csv(args.universe, dtype={"symbol": str})
    symbols = sorted(set(universe["symbol"].astype(str).str.zfill(6)))
    stock_prices = pd.read_csv(args.stock_prices, dtype={"symbol": str})
    stock_errors = pd.read_csv(args.stock_errors)
    stock_report = json.loads(args.stock_report.read_text(encoding="utf-8"))
    strict = pd.read_csv(args.strict_daily_coverage)
    eastmoney = pd.read_csv(args.eastmoney_index, dtype={"index_code": str})

    official = fetch_csindex_history(
        INNOVATION_DRUG_INDEX,
        start_date=DESIGN_START.strftime("%Y-%m-%d"),
        end_date=DESIGN_END.strftime("%Y-%m-%d"),
        retries=2,
        retry_backoff_seconds=0.75,
        timeout_seconds=20.0,
    )

    strict_dates = pd.DatetimeIndex(pd.to_datetime(strict["date"], errors="raise")).normalize()
    official_dates = pd.DatetimeIndex(pd.to_datetime(official["date"], errors="raise")).normalize()
    strict_set = set(strict_dates)
    official_set = set(official_dates)
    missing_official_dates = sorted(strict_set - official_set)
    unexpected_official_dates = sorted(official_set - strict_set)

    eastmoney["date"] = pd.to_datetime(eastmoney["date"], errors="raise").dt.normalize()
    eastmoney["index_code"] = eastmoney["index_code"].astype(str).str.zfill(6)
    if set(eastmoney["index_code"]) != {INNOVATION_DRUG_INDEX}:
        raise ValueError("EastMoney cross-check rail has an unexpected index code")
    overlap = official[["date", "close"]].merge(
        eastmoney[["date", "close"]],
        on="date",
        how="inner",
        suffixes=("_official", "_eastmoney"),
        validate="one_to_one",
    )
    overlap["abs_close_diff"] = (
        pd.to_numeric(overlap["close_official"], errors="raise")
        - pd.to_numeric(overlap["close_eastmoney"], errors="raise")
    ).abs()
    overlap_mismatch = overlap[overlap["abs_close_diff"] > OVERLAP_CLOSE_ATOL].copy()

    audit = audit_design_price_inputs(stock_prices, official, symbols)
    errors = list(audit.errors)
    if len(stock_errors) != 0:
        errors.append(f"stock download errors are non-empty: {len(stock_errors)}")
    if stock_report.get("status") != "DESIGN_PRICE_INPUT_ELIGIBLE":
        errors.append("source stock-price qualification is not eligible")
    if missing_official_dates:
        errors.append(f"official CSI rail missing strict trading dates: {len(missing_official_dates)}")
    if unexpected_official_dates:
        errors.append(f"official CSI rail has unexpected dates: {len(unexpected_official_dates)}")
    if len(overlap) != len(eastmoney):
        errors.append(
            f"EastMoney cross-check dates not fully covered by CSI: overlap={len(overlap)} eastmoney={len(eastmoney)}"
        )
    if not overlap.empty and overlap_mismatch.shape[0] > 0:
        errors.append(f"official/EastMoney close mismatch days: {len(overlap_mismatch)}")

    official_path = out / "index_931152_prices.csv"
    overlap_path = out / "index_931152_eastmoney_overlap.csv"
    stock_prices_path = out / "stock_prices_qfq.csv"
    stock_errors_path = out / "stock_download_errors.csv"
    official.to_csv(official_path, index=False, date_format="%Y-%m-%d")
    overlap.to_csv(overlap_path, index=False, date_format="%Y-%m-%d")
    stock_prices.to_csv(stock_prices_path, index=False, date_format="%Y-%m-%d")
    stock_errors.to_csv(stock_errors_path, index=False)

    max_overlap_diff = float(overlap["abs_close_diff"].max()) if not overlap.empty else None
    report = {
        "status": "DESIGN_PRICE_INPUT_ELIGIBLE" if not errors else "DESIGN_PRICE_INPUT_INSUFFICIENT",
        "qualification_revision": 2,
        "qualification_scope": "public_design_input_only",
        "index_code": INNOVATION_DRUG_INDEX,
        "design_start": DESIGN_START.strftime("%Y-%m-%d"),
        "design_end": DESIGN_END.strftime("%Y-%m-%d"),
        "stock_adjustment": stock_report.get("stock_adjustment"),
        "stock_source_run_id": stock_report.get("stock_source_run_id"),
        "stock_artifact_digest": stock_report.get("stock_artifact_digest"),
        "requested_symbols": len(symbols),
        "stock_rows": int(len(stock_prices)),
        "stock_symbols": int(stock_prices["symbol"].astype(str).str.zfill(6).nunique()),
        "download_error_rows": int(len(stock_errors)),
        "index_rows": int(len(official)),
        "index_start": official["date"].min().strftime("%Y-%m-%d"),
        "index_end": official["date"].max().strftime("%Y-%m-%d"),
        "strict_trading_dates": int(len(strict_dates)),
        "missing_official_dates": [item.strftime("%Y-%m-%d") for item in missing_official_dates],
        "unexpected_official_dates": [item.strftime("%Y-%m-%d") for item in unexpected_official_dates],
        "index_primary_provider": "csindex:index_perf",
        "index_primary_source_url": CSINDEX_INDEX_PERF_URL,
        "index_primary_query": {
            "indexCode": INNOVATION_DRUG_INDEX,
            "startDate": DESIGN_START.strftime("%Y%m%d"),
            "endDate": DESIGN_END.strftime("%Y%m%d"),
        },
        "crosscheck_provider": "eastmoney:direct_sector_index_kline",
        "crosscheck_rows": int(len(overlap)),
        "crosscheck_expected_rows": int(len(eastmoney)),
        "crosscheck_start": overlap["date"].min().strftime("%Y-%m-%d") if not overlap.empty else None,
        "crosscheck_end": overlap["date"].max().strftime("%Y-%m-%d") if not overlap.empty else None,
        "crosscheck_close_atol": OVERLAP_CLOSE_ATOL,
        "crosscheck_max_abs_close_diff": max_overlap_diff,
        "crosscheck_mismatch_rows": int(len(overlap_mismatch)),
        "audit_errors": errors,
        "files": {},
        "model_events_computed": False,
        "forward_outcomes_computed": False,
        "holdout_opened": False,
    }
    for path in (official_path, overlap_path, stock_prices_path, stock_errors_path):
        report["files"][path.name] = _sha256_file(path)

    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
