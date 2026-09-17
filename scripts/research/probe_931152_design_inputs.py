from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.data_akshare import download_universe_history
from tech_sentiment.sector_design_input_contract import (
    DESIGN_END,
    DESIGN_START,
    INNOVATION_DRUG_INDEX,
    STOCK_ADJUSTMENT,
    STOCK_WARMUP_START,
    audit_design_price_inputs,
)
from tech_sentiment.sector_index_price import fetch_sector_index_history_direct


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free public price input probe for the 931152 design sample. "
            "It never computes sector events, forward returns, MAE or MFE."
        )
    )
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.universe, dtype={"symbol": str})
    requested_symbols = sorted(set(universe["symbol"].astype(str).str.zfill(6)))

    download = download_universe_history(
        universe,
        start_date=STOCK_WARMUP_START.strftime("%Y-%m-%d"),
        end_date=DESIGN_END.strftime("%Y-%m-%d"),
        adjust=STOCK_ADJUSTMENT,
        providers=("eastmoney", "tencent"),
        retries=1,
        retry_backoff_seconds=0.75,
        sleep_seconds=0.0,
        fail_fast=False,
    )
    prices = download.prices.copy()
    errors = download.errors.copy()

    # Persist the expensive stock download before the independent index request.
    # If an index provider fails, the run still leaves auditable partial evidence.
    prices_path = out / "stock_prices_qfq.csv"
    errors_path = out / "stock_download_errors.csv"
    index_path = out / "index_931152_prices.csv"
    prices.to_csv(prices_path, index=False, date_format="%Y-%m-%d")
    errors.to_csv(errors_path, index=False)

    index_prices = fetch_sector_index_history_direct(
        INNOVATION_DRUG_INDEX,
        start_date=DESIGN_START.strftime("%Y-%m-%d"),
        end_date=DESIGN_END.strftime("%Y-%m-%d"),
        retries=2,
        retry_backoff_seconds=0.75,
        timeout_seconds=15.0,
    )
    index_prices.to_csv(index_path, index=False, date_format="%Y-%m-%d")

    audit = audit_design_price_inputs(prices, index_prices, requested_symbols)
    provider_distribution = (
        {
            str(key): int(value)
            for key, value in prices["provider"].astype(str).value_counts().sort_index().items()
        }
        if "provider" in prices.columns
        else {}
    )
    report = {
        "status": (
            "DESIGN_PRICE_INPUT_ELIGIBLE"
            if audit.eligible_for_design_input
            else "DESIGN_PRICE_INPUT_INSUFFICIENT"
        ),
        "qualification_scope": "public_design_input_only",
        "index_code": INNOVATION_DRUG_INDEX,
        "stock_warmup_start": STOCK_WARMUP_START.strftime("%Y-%m-%d"),
        "design_start": DESIGN_START.strftime("%Y-%m-%d"),
        "design_end": DESIGN_END.strftime("%Y-%m-%d"),
        "stock_adjustment": STOCK_ADJUSTMENT,
        "stock_provider_order": ["eastmoney", "tencent"],
        "requested_symbols": len(requested_symbols),
        "stock_rows": audit.stock_rows,
        "stock_symbols": audit.stock_symbols,
        "index_rows": audit.index_rows,
        "missing_symbols": list(audit.missing_symbols),
        "download_error_rows": int(len(errors)),
        "stock_provider_distribution": provider_distribution,
        "index_provider_distribution": (
            {
                str(key): int(value)
                for key, value in index_prices["provider"]
                .astype(str)
                .value_counts()
                .sort_index()
                .items()
            }
            if "provider" in index_prices.columns
            else {}
        ),
        "index_provider_identifiers": (
            sorted(set(index_prices["provider_identifier"].astype(str)))
            if "provider_identifier" in index_prices.columns
            else []
        ),
        "audit_errors": list(audit.errors),
        "files": {
            prices_path.name: _sha256_file(prices_path),
            errors_path.name: _sha256_file(errors_path),
            index_path.name: _sha256_file(index_path),
        },
        "model_events_computed": False,
        "forward_outcomes_computed": False,
        "holdout_opened": False,
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
