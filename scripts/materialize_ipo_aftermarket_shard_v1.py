from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.data_akshare import fetch_stock_history
from tech_sentiment.ipo_aftermarket_public_v1 import (
    PRICE_COLUMNS,
    UNLOCK_COLUMNS,
    UNLOCK_STATUS_COLUMNS,
    allowlist_prices,
    normalize_unlock_queue,
)


def _hash(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _akshare():
    import akshare as ak
    return ak


def _fetch_price(symbol: str, listing_date: pd.Timestamp, as_of: pd.Timestamp, ak) -> pd.DataFrame:
    end = min(as_of, listing_date + pd.Timedelta(days=550))
    errors: list[str] = []
    for provider in ("eastmoney", "tencent"):
        for attempt in range(2):
            try:
                frame = fetch_stock_history(
                    symbol,
                    start_date=listing_date.date().isoformat(),
                    end_date=end.date().isoformat(),
                    adjust="",
                    provider=provider,
                    timeout_seconds=20.0,
                    client=ak,
                )
                if not frame.empty:
                    frame = frame[frame["date"].ge(listing_date)].head(120)
                    return allowlist_prices(frame)
                errors.append(f"{provider}[{attempt+1}]: empty")
            except Exception as exc:
                errors.append(f"{provider}[{attempt+1}]: {type(exc).__name__}: {exc}")
    raise RuntimeError(" | ".join(errors))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--metadata", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--shard-index", type=int, required=True)
    p.add_argument("--shard-count", type=int, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard index")

    metadata = pd.read_csv(args.metadata, dtype={"symbol": str})
    metadata["symbol"] = metadata["symbol"].astype(str).str.zfill(6)
    metadata["listing_date"] = pd.to_datetime(metadata["listing_date"], errors="raise").dt.normalize()
    selected = metadata.sort_values("symbol").reset_index(drop=True)
    selected = selected.iloc[args.shard_index :: args.shard_count].copy()
    as_of = pd.Timestamp(args.as_of).normalize()
    ak = _akshare()

    price_parts: list[pd.DataFrame] = []
    unlock_parts: list[pd.DataFrame] = []
    unlock_status: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for row in selected.itertuples(index=False):
        symbol = str(row.symbol).zfill(6)
        try:
            price_parts.append(_fetch_price(symbol, row.listing_date, as_of, ak))
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "price", "error": f"{type(exc).__name__}: {exc}"})

        provider = "akshare:stock_restricted_release_queue_em"
        try:
            raw = ak.stock_restricted_release_queue_em(symbol=symbol)
            normalized = normalize_unlock_queue(raw, symbol=symbol, provider=provider)
            if not normalized.empty:
                lower = row.listing_date - pd.Timedelta(days=30)
                upper = min(as_of + pd.Timedelta(days=30), row.listing_date + pd.Timedelta(days=550))
                normalized = normalized[
                    normalized["unlock_date"].between(lower, upper, inclusive="both")
                ].copy()
                if not normalized.empty:
                    unlock_parts.append(normalized)
            unlock_status.append({"symbol": symbol, "status": "QUERY_OK", "provider": provider, "error": ""})
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            unlock_status.append({"symbol": symbol, "status": "QUERY_ERROR", "provider": provider, "error": message})
            errors.append({"symbol": symbol, "stage": "unlock", "error": message})

    prices = (
        pd.concat(price_parts, ignore_index=True)
        if price_parts
        else pd.DataFrame(columns=PRICE_COLUMNS)
    )
    unlocks = (
        pd.concat(unlock_parts, ignore_index=True)
        if unlock_parts
        else pd.DataFrame(columns=UNLOCK_COLUMNS)
    )
    statuses = pd.DataFrame(unlock_status, columns=UNLOCK_STATUS_COLUMNS)
    err = pd.DataFrame(errors, columns=["symbol", "stage", "error"])

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    prices.to_csv(out / "prices.csv", index=False, date_format="%Y-%m-%d")
    unlocks.to_csv(out / "unlocks.csv", index=False, date_format="%Y-%m-%d")
    statuses.to_csv(out / "unlock_status.csv", index=False)
    err.to_csv(out / "errors.csv", index=False)

    receipt = {
        "schema_version": "ipo-aftermarket-public-shard-v1",
        "source_commit": args.source_commit,
        "as_of": args.as_of,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "selected_symbols": int(len(selected)),
        "price_symbols": int(prices["symbol"].nunique()) if not prices.empty else 0,
        "unlock_query_ok": int(statuses["status"].eq("QUERY_OK").sum()),
        "errors": int(len(err)),
        "files": {
            name: {"sha256": _hash(out / name), "bytes": (out / name).stat().st_size}
            for name in ("prices.csv", "unlocks.csv", "unlock_status.csv", "errors.csv")
        },
        "public_only": True,
        "research_results_present": False,
    }
    (out / "shard_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
