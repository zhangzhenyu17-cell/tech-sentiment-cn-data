from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.data_akshare import download_universe_history
from tech_sentiment.index_history import (
    read_adjustments_csv,
    read_anchor_csv,
    reconstruct_index_history,
)
from tech_sentiment.index_price import fetch_index_history


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip_membership(
    frame: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    out = frame.copy()
    out["effective_start"] = pd.to_datetime(out["effective_start"], errors="raise").dt.normalize()
    out["effective_end"] = pd.to_datetime(out["effective_end"], errors="raise").dt.normalize()
    out = out[
        out["effective_start"].le(end) & out["effective_end"].ge(start)
    ].copy()
    if out.empty:
        raise ValueError("point-in-time membership has no rows in the Phase A window")
    out["effective_start"] = out["effective_start"].clip(lower=start)
    out["effective_end"] = out["effective_end"].clip(upper=end)
    return out.sort_values(["effective_start", "symbol"]).reset_index(drop=True)


def _validate_daily_membership(
    membership: pd.DataFrame,
    trading_dates: pd.Series,
    *,
    expected_constituents: int,
    universe: str,
) -> dict[str, object]:
    dates = pd.to_datetime(trading_dates, errors="raise").dt.normalize()
    starts = pd.to_datetime(membership["effective_start"], errors="raise").dt.normalize()
    ends = pd.to_datetime(membership["effective_end"], errors="raise").dt.normalize()
    counts: list[int] = []
    for date in dates:
        active = membership.loc[starts.le(date) & ends.ge(date), "symbol"].astype(str)
        n = int(active.nunique())
        counts.append(n)
        if n != expected_constituents:
            raise ValueError(
                f"{universe} membership count is {n} on {date.date()}, "
                f"expected {expected_constituents}"
            )
    if not counts:
        raise ValueError(f"{universe} official index rail returned no Phase A trading dates")
    return {
        "trading_days": len(counts),
        "min_active_constituents": min(counts),
        "max_active_constituents": max(counts),
    }


def _write_frame(frame: pd.DataFrame, path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, date_format="%Y-%m-%d")
    return {
        "path": path.as_posix(),
        "rows": int(len(frame)),
        "sha256": _sha256(path),
        "bytes": int(path.stat().st_size),
    }


def _materialize_universe(
    *,
    repo_root: Path,
    out_root: Path,
    universe: str,
    config: dict[str, object],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, object], list[dict[str, object]]]:
    index_code = str(config["index_code"])
    expected = int(config["expected_constituents"])
    anchor_date = pd.Timestamp(str(config["anchor_effective_date"])).normalize()
    anchor_path = repo_root / str(config["anchor_path"])
    adjustments_path = repo_root / str(config["adjustments_path"])

    anchor = read_anchor_csv(anchor_path)
    adjustments = read_adjustments_csv(adjustments_path)
    membership_full, segments_full, diagnostics = reconstruct_index_history(
        anchor,
        adjustments,
        history_start=start,
        history_end=anchor_date,
        anchor_effective_date=anchor_date,
        expected_constituents=expected,
        index_code=index_code,
    )
    membership = _clip_membership(membership_full, start=start, end=end)

    index_prices = fetch_index_history(
        index_code,
        start_date=start.date().isoformat(),
        end_date=end.date().isoformat(),
    )
    if index_prices.empty:
        raise ValueError(f"{universe} official index rail is empty")
    index_prices["date"] = pd.to_datetime(index_prices["date"], errors="raise").dt.normalize()
    index_prices = index_prices[
        index_prices["date"].ge(start) & index_prices["date"].le(end)
    ].copy()
    membership_audit = _validate_daily_membership(
        membership,
        index_prices["date"],
        expected_constituents=expected,
        universe=universe,
    )

    result = download_universe_history(
        membership,
        start_date=start.date().isoformat(),
        end_date=end.date().isoformat(),
        adjust="qfq",
        providers=("tencent", "eastmoney"),
        retries=1,
        retry_backoff_seconds=0.75,
        sleep_seconds=0.05,
        timeout_seconds=20.0,
        fail_fast=False,
    )
    expected_symbols = int(membership["symbol"].astype(str).nunique())
    downloaded_symbols = (
        int(result.prices["symbol"].astype(str).nunique()) if not result.prices.empty else 0
    )
    coverage = downloaded_symbols / expected_symbols if expected_symbols else 0.0
    if coverage < 0.95:
        raise ValueError(
            f"{universe} constituent-price symbol coverage {coverage:.1%} is below 95%"
        )

    root = out_root / universe
    files = [
        _write_frame(membership, root / "universe_point_in_time.csv"),
        _write_frame(
            segments_full[
                (pd.to_datetime(segments_full["effective_start"]).le(end))
                & (pd.to_datetime(segments_full["effective_end"]).ge(start))
            ].copy(),
            root / "universe_segments_audit.csv",
        ),
        _write_frame(result.prices, root / "prices.csv"),
        _write_frame(result.errors, root / "download_errors.csv"),
        _write_frame(index_prices, root / "index_prices.csv"),
    ]
    summary = {
        "index_code": index_code,
        "anchor_path": str(config["anchor_path"]),
        "anchor_effective_date": anchor_date.date().isoformat(),
        "adjustments_path": str(config["adjustments_path"]),
        "expected_constituents": expected,
        "membership_rows": int(len(membership)),
        "unique_symbols": expected_symbols,
        "price_rows": int(len(result.prices)),
        "downloaded_symbols": downloaded_symbols,
        "download_errors": int(len(result.errors)),
        "symbol_coverage": coverage,
        "membership_audit": membership_audit,
        "reverse_reconstruction": {
            "adjustment_dates": int(diagnostics.adjustment_dates),
            "adjustment_rows": int(diagnostics.adjustment_rows),
            "unique_symbols_full_chain": int(diagnostics.unique_symbols),
            "segments_full_chain": int(diagnostics.segments),
        },
    }
    return membership, summary, files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the public-only V4C-03 Phase A PIT universe and price bundle."
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    repo_root = args.contract.resolve().parents[1]
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "v4c03_phase_a_public_universe_v1":
        raise ValueError("unexpected V4C-03 public Phase A contract")
    if contract.get("status") != "FROZEN_PUBLIC_DATA_SCOPE":
        raise ValueError("V4C-03 public Phase A contract is not frozen")
    for key in (
        "private_model_semantics_allowed",
        "portfolio_or_holdings_data_allowed",
        "forward_result_computation_allowed",
        "automatic_trigger_allowed",
        "production_or_trading_authority_changed",
    ):
        if contract.get(key) is not False:
            raise ValueError(f"public boundary drift: {key}")

    start = pd.Timestamp(contract["window"]["start_date"]).normalize()
    end = pd.Timestamp(contract["window"]["end_date"]).normalize()
    out_root = args.out_dir.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    memberships: dict[str, pd.DataFrame] = {}
    summaries: dict[str, object] = {}
    files: list[dict[str, object]] = []
    for universe in ("STAR50", "ChiNext50"):
        membership, summary, universe_files = _materialize_universe(
            repo_root=repo_root,
            out_root=out_root,
            universe=universe,
            config=contract["universes"][universe],
            start=start,
            end=end,
        )
        memberships[universe] = membership
        summaries[universe] = summary
        files.extend(universe_files)

    symbol_scopes: dict[str, set[str]] = {}
    for universe, membership in memberships.items():
        for symbol in membership["symbol"].astype(str).str.zfill(6):
            symbol_scopes.setdefault(symbol, set()).add(universe)
    scope_rows = [
        {
            "symbol": symbol,
            "market": "SH" if symbol.startswith(("6", "9")) else "SZ",
            "historical_scope": ";".join(sorted(scopes)),
        }
        for symbol, scopes in sorted(symbol_scopes.items())
    ]
    scope = pd.DataFrame(scope_rows)
    files.append(
        _write_frame(
            scope,
            out_root / "pit_symbol_scope" / "capital_pit_symbols.csv",
        )
    )

    input_files = {
        path: _sha256(repo_root / path)
        for path in sorted(
            {
                str(contract["universes"]["STAR50"]["anchor_path"]),
                str(contract["universes"]["STAR50"]["adjustments_path"]),
                str(contract["universes"]["ChiNext50"]["anchor_path"]),
                str(contract["universes"]["ChiNext50"]["adjustments_path"]),
            }
        )
    }
    receipt = {
        "schema_version": "v4c03-phase-a-public-universe-receipt-v1",
        "status": "PUBLIC_PIT_UNIVERSE_AND_PRICES_MATERIALIZED",
        "source_commit": args.source_commit,
        "phase": "A",
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "universes": summaries,
        "symbol_scope": {
            "symbols": int(len(scope)),
            "sh_symbols": int(scope["market"].eq("SH").sum()),
            "sz_symbols": int(scope["market"].eq("SZ").sum()),
        },
        "input_file_sha256": input_files,
        "files": files,
        "public_only": True,
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "forward_result_computation_run": False,
        "production_or_trading_authority_changed": False,
    }
    receipt_path = out_root / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
