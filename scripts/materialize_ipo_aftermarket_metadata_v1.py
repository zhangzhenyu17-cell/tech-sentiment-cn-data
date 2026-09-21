from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.ipo_aftermarket_public_v1 import METADATA_COLUMNS, normalize_ipo_metadata


def _hash(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _akshare():
    import akshare as ak
    return ak


def _fetch_metadata(ak) -> tuple[pd.DataFrame, str, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    try:
        raw = ak.stock_dxsyl_em()
        return normalize_ipo_metadata(raw, provider="akshare:stock_dxsyl_em"), "stock_dxsyl_em", errors
    except Exception as exc:
        errors.append({"stage": "stock_dxsyl_em", "error": f"{type(exc).__name__}: {exc}"})
    try:
        raw = ak.stock_xgsglb_em(symbol="全部股票")
        return normalize_ipo_metadata(raw, provider="akshare:stock_xgsglb_em"), "stock_xgsglb_em", errors
    except Exception as exc:
        errors.append({"stage": "stock_xgsglb_em", "error": f"{type(exc).__name__}: {exc}"})
        raise RuntimeError("all IPO metadata providers failed") from exc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "ipo_aftermarket_public_v1":
        raise ValueError("unexpected IPO public contract")
    if contract.get("status") != "FROZEN_PUBLIC_INPUT_SCOPE":
        raise ValueError("IPO public contract is not frozen")
    for key in (
        "private_model_semantics_allowed",
        "private_thresholds_allowed",
        "portfolio_or_holdings_data_allowed",
        "research_results_allowed",
        "forward_result_computation_allowed",
        "automatic_trigger_allowed",
    ):
        if contract.get(key) is not False:
            raise ValueError(f"public boundary drift: {key}")

    as_of = pd.Timestamp(args.as_of).normalize()
    if as_of.date().isoformat() != contract["materialization_as_of"]:
        raise ValueError("as-of date differs from frozen public materialization date")
    start = pd.Timestamp(contract["listing_start"]).normalize()

    ak = _akshare()
    metadata, provider, errors = _fetch_metadata(ak)
    metadata = metadata[
        metadata["listing_date"].ge(start) & metadata["listing_date"].le(as_of)
    ].copy()
    if metadata.empty:
        raise ValueError("no IPO metadata in frozen listing window")

    benchmark = fetch_index_history(
        contract["benchmark"]["index_code"],
        start_date=contract["benchmark"]["start_date"],
        end_date=args.as_of,
        client=ak,
    )
    if benchmark.empty:
        raise ValueError("broad-A benchmark rail is empty")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    metadata = metadata.loc[:, METADATA_COLUMNS]
    metadata.to_csv(out / "ipo_metadata.csv", index=False, date_format="%Y-%m-%d")
    benchmark.to_csv(out / "benchmark_prices.csv", index=False, date_format="%Y-%m-%d")
    pd.DataFrame(errors, columns=["stage", "error"]).to_csv(
        out / "metadata_errors.csv", index=False
    )
    receipt = {
        "schema_version": "ipo-aftermarket-public-metadata-v1",
        "status": "PUBLIC_METADATA_AND_BENCHMARK_MATERIALIZED",
        "source_commit": args.source_commit,
        "as_of": args.as_of,
        "metadata_provider": provider,
        "metadata_rows": int(len(metadata)),
        "benchmark_rows": int(len(benchmark)),
        "files": {
            name: {"sha256": _hash(out / name), "bytes": (out / name).stat().st_size}
            for name in ("ipo_metadata.csv", "benchmark_prices.csv", "metadata_errors.csv")
        },
        "public_only": True,
        "private_model_semantics_present": False,
        "research_results_present": False,
    }
    (out / "metadata_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
