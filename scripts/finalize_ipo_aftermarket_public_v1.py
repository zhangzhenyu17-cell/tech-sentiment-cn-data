from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pandas as pd

from tech_sentiment.ipo_aftermarket_public_v1 import (
    METADATA_COLUMNS,
    PRICE_COLUMNS,
    UNLOCK_COLUMNS,
    UNLOCK_STATUS_COLUMNS,
    validate_public_bundle,
)


def _hash(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _concat(paths: list[Path], columns: list[str], *, dtype=None) -> pd.DataFrame:
    parts = [pd.read_csv(path, dtype=dtype) for path in paths if path.exists() and path.stat().st_size]
    if not parts:
        return pd.DataFrame(columns=columns)
    out = pd.concat(parts, ignore_index=True)
    return out.loc[:, columns]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--metadata-dir", type=Path, required=True)
    p.add_argument("--shards-dir", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "ipo_aftermarket_public_v1":
        raise ValueError("unexpected IPO public contract")
    if args.as_of != contract["materialization_as_of"]:
        raise ValueError("as-of date differs from frozen contract")

    metadata = pd.read_csv(args.metadata_dir / "ipo_metadata.csv", dtype={"symbol": str})
    metadata["symbol"] = metadata["symbol"].astype(str).str.zfill(6)
    metadata = metadata.loc[:, METADATA_COLUMNS]
    price_paths = sorted(args.shards_dir.rglob("prices.csv"))
    unlock_paths = sorted(args.shards_dir.rglob("unlocks.csv"))
    status_paths = sorted(args.shards_dir.rglob("unlock_status.csv"))
    error_paths = sorted(args.shards_dir.rglob("errors.csv"))
    if not price_paths or not status_paths:
        raise ValueError("missing shard outputs")

    prices = _concat(price_paths, PRICE_COLUMNS, dtype={"symbol": str})
    unlocks = _concat(unlock_paths, UNLOCK_COLUMNS, dtype={"symbol": str})
    statuses = _concat(status_paths, UNLOCK_STATUS_COLUMNS, dtype={"symbol": str})
    errors = _concat(error_paths, ["symbol", "stage", "error"], dtype={"symbol": str})

    for frame in (prices, unlocks, statuses, errors):
        if "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    prices["date"] = pd.to_datetime(prices["date"], errors="raise").dt.normalize()
    if not unlocks.empty:
        unlocks["unlock_date"] = pd.to_datetime(unlocks["unlock_date"], errors="raise").dt.normalize()

    audit = validate_public_bundle(
        metadata,
        prices,
        unlocks,
        statuses,
        minimum_price_symbol_coverage=float(contract["minimum_price_symbol_coverage"]),
    )

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    metadata.to_csv(out / "ipo_metadata.csv", index=False, date_format="%Y-%m-%d")
    prices.sort_values(["symbol", "date"]).to_csv(
        out / "ipo_prices.csv", index=False, date_format="%Y-%m-%d"
    )
    unlocks.sort_values(["symbol", "unlock_date"]).to_csv(
        out / "ipo_unlocks.csv", index=False, date_format="%Y-%m-%d"
    )
    statuses.sort_values("symbol").to_csv(out / "ipo_unlock_status.csv", index=False)
    errors.sort_values(["symbol", "stage"]).to_csv(out / "download_errors.csv", index=False)
    shutil.copyfile(args.metadata_dir / "benchmark_prices.csv", out / "benchmark_prices.csv")

    files = {}
    for name in (
        "ipo_metadata.csv",
        "ipo_prices.csv",
        "ipo_unlocks.csv",
        "ipo_unlock_status.csv",
        "benchmark_prices.csv",
        "download_errors.csv",
    ):
        path = out / name
        files[name] = {
            "sha256": _hash(path),
            "bytes": path.stat().st_size,
            "rows": max(0, sum(1 for _ in path.open("r", encoding="utf-8")) - 1),
        }

    manifest = {
        "schema_version": "ipo-aftermarket-public-bundle-v1",
        "contract_id": contract["contract_id"],
        "status": "PUBLIC_INPUT_BUNDLE_QUALIFIED",
        "source_commit": args.source_commit,
        "as_of": args.as_of,
        "stock_price_adjustment": "NONE",
        "max_post_listing_sessions": contract["max_post_listing_sessions"],
        "benchmark_index_code": contract["benchmark"]["index_code"],
        "audit": audit.__dict__,
        "unlock_query_errors": int(statuses["status"].eq("QUERY_ERROR").sum()),
        "files": files,
        "public_only": True,
        "private_model_semantics_present": False,
        "private_thresholds_present": False,
        "portfolio_or_holdings_data_present": False,
        "research_results_present": False,
        "forward_result_computation_run": False,
        "production_or_trading_authority_changed": False,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
