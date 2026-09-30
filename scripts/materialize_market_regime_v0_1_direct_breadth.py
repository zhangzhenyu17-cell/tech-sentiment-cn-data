from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import datetime

import pandas as pd
import requests

from tech_sentiment.market_regime_breadth_v0_1 import (
    compute_direct_security_breadth,
    write_bundle_manifest,
)

TX_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"


def _live_snapshot() -> tuple[pd.DataFrame, int]:
    import akshare as ak
    raw = ak.stock_zh_a_spot()
    required = {"代码", "名称", "最新价", "昨收", "时间戳"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Sina A-share snapshot missing columns: {sorted(missing)}")
    code = raw["代码"].astype(str)
    bse_count = int(code.str.startswith("bj").sum())
    selected = raw.loc[code.str.startswith(("sh", "sz"))].copy()
    selected["market_prefix"] = selected["代码"].astype(str).str[:2]
    selected["symbol"] = selected["代码"].astype(str).str.extract(r"(\d{6})", expand=False)
    # B shares are not A shares even if a provider page happens to expose them.
    selected = selected.loc[~selected["symbol"].str.startswith(("900", "200"), na=False)].copy()
    selected = selected.rename(columns={"名称": "name", "最新价": "latest", "昨收": "prev_close", "时间戳": "timestamp"})
    return selected[["symbol", "name", "latest", "prev_close", "timestamp", "market_prefix"]].reset_index(drop=True), bse_count


def _tx_symbol(symbol: str) -> str:
    code = str(symbol).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_one(symbol: str, *, market_date: str, timeout: float, retries: int) -> tuple[pd.DataFrame, str | None]:
    code = str(symbol).zfill(6)
    tx = _tx_symbol(code)
    params = {
        "_var": "kline_day_unadjusted",
        "param": f"{tx},day,2024-01-01,{market_date},640,",
        "r": "0.8205512681390605",
    }
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            r = requests.get(TX_URL, params=params, timeout=timeout)
            r.raise_for_status()
            text = r.text
            start = text.find("={")
            if start < 0:
                raise ValueError("Tencent response missing JSON assignment")
            payload = json.loads(text[start + 1 :])
            node = payload.get("data", {}).get(tx, {})
            rows = node.get("day") or node.get("qfqday") or node.get("hfqday")
            if not rows:
                raise ValueError("Tencent response has no daily rows")
            frame = pd.DataFrame(rows)
            if frame.shape[1] < 3:
                raise ValueError("Tencent daily row width too small")
            out = pd.DataFrame({
                "date": frame.iloc[:, 0],
                "symbol": code,
                "close": frame.iloc[:, 2],
                "provider": "tencent:direct_newfqkline_unadjusted",
            })
            out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
            out["close"] = pd.to_numeric(out["close"], errors="raise")
            out = out.loc[out["date"] <= pd.Timestamp(market_date)].drop_duplicates("date", keep="last").sort_values("date")
            return out.reset_index(drop=True), None
        except Exception as exc:
            errors.append(f"attempt{attempt + 1}:{type(exc).__name__}:{exc}")
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
    return pd.DataFrame(columns=["date", "symbol", "close", "provider"]), " | ".join(errors)


def _checkpoint_path(checkpoint_dir: Path, symbol: str) -> Path:
    return checkpoint_dir / f"{str(symbol).zfill(6)}.csv.gz"


def materialize(*, market_date: str, output_dir: Path, checkpoint_dir: Path, workers: int, timeout: float, retries: int) -> dict[str, Any]:
    snapshot, bse_excluded = _live_snapshot()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "security_snapshot.csv"
    snapshot.to_csv(snapshot_path, index=False)

    histories: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    complete = 0
    pending: list[str] = []
    for symbol in snapshot["symbol"].astype(str):
        cp = _checkpoint_path(checkpoint_dir, symbol)
        if cp.is_file():
            frame = pd.read_csv(cp, parse_dates=["date"], dtype={"symbol": str})
            histories.append(frame)
            complete += 1
        else:
            pending.append(symbol)

    def task(symbol: str) -> tuple[str, pd.DataFrame, str | None]:
        frame, error = _fetch_one(symbol, market_date=market_date, timeout=timeout, retries=retries)
        return symbol, frame, error

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task, symbol): symbol for symbol in pending}
            for future in as_completed(futures):
                symbol, frame, error = future.result()
                if error is not None or frame.empty:
                    errors.append({"symbol": symbol, "error": error or "EMPTY_HISTORY"})
                    continue
                cp = _checkpoint_path(checkpoint_dir, symbol)
                frame.to_csv(cp, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
                histories.append(frame)
                complete += 1

    history = pd.concat(histories, ignore_index=True, sort=False) if histories else pd.DataFrame(columns=["date", "symbol", "close", "provider"])
    history["symbol"] = history["symbol"].astype(str).str.zfill(6)
    history = history.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)
    history_path = output_dir / "security_close_history.csv.gz"
    history.to_csv(history_path, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
    errors_path = output_dir / "query_errors.csv"
    pd.DataFrame(errors, columns=["symbol", "error"]).to_csv(errors_path, index=False)

    diagnostics, summary = compute_direct_security_breadth(
        snapshot,
        history,
        market_date=market_date,
        query_coverage={
            "query_symbol_count": len(snapshot),
            "complete_query_count": complete,
            "query_error_count": len(errors),
        },
    )
    summary["captured_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    summary["beijing_snapshot_security_count_excluded"] = bse_excluded
    diagnostics_path = output_dir / "security_diagnostics.csv"
    diagnostics.to_csv(diagnostics_path, index=False)
    summary_path = output_dir / "breadth_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "market_date": market_date,
        "query_symbol_count": len(snapshot),
        "complete_query_count": complete,
        "query_error_count": len(errors),
        "workers": workers,
        "timeout_seconds": timeout,
        "retries": retries,
        "checkpoint_dir_committed": False,
        "source_endpoint": TX_URL,
        "source_adjustment": "NONE",
    }
    receipt_path = output_dir / "capture_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    root = Path.cwd()
    rels = [str(path.resolve().relative_to(root.resolve())) for path in (snapshot_path, history_path, diagnostics_path, summary_path, errors_path, receipt_path)]
    manifest_path = output_dir / "bundle_manifest.json"
    manifest = write_bundle_manifest(root, relative_paths=rels, output_path=manifest_path, market_date=market_date)
    return {"summary": summary, "receipt": receipt, "bundle": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize direct SSE/SZSE security-level breadth for Market Regime V0.1.")
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    result = materialize(market_date=args.market_date, output_dir=args.output_dir, checkpoint_dir=args.checkpoint_dir, workers=args.workers, timeout=args.timeout, retries=args.retries)
    print(json.dumps({
        "status": result["summary"]["status"],
        "universe_security_count": result["summary"]["universe_security_count"],
        "complete_query_count": result["summary"]["complete_query_count"],
        "query_error_count": result["summary"]["query_error_count"],
        "same_day_history_security_count": result["summary"]["same_day_history_security_count"],
        "qualified_trend_breadth": result["summary"]["qualified_trend_breadth"],
        "breadth": result["summary"]["breadth"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
