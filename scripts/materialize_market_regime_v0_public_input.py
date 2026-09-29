from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.index_price import fetch_index_history


INDEX_CODES = ("000985", "000300", "000905", "399006")
SCHEMA_VERSION = "market-regime-v0-public-input-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_market_regime_public_input(
    *,
    start_date: str,
    as_of_date: str,
    output_csv: Path,
    output_manifest: Path,
    fetcher=fetch_index_history,
) -> dict:
    frames = []
    providers: set[str] = set()
    for code in INDEX_CODES:
        frame = fetcher(
            code,
            start_date=start_date,
            end_date=as_of_date,
            retries=2,
            retry_backoff_seconds=0.5,
        ).copy()
        if frame.empty:
            raise ValueError(f"no public index history for {code}")
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        if (frame["date"] > pd.Timestamp(as_of_date)).any():
            raise ValueError(f"future row detected for {code}")
        if frame["date"].duplicated().any():
            raise ValueError(f"duplicate index date for {code}")
        if len(frame) < 121:
            raise ValueError(f"insufficient history for {code}: {len(frame)} < 121")
        if frame["index_code"].astype(str).str.zfill(6).ne(code).any():
            raise ValueError(f"index identity mismatch for {code}")
        if frame["close"].isna().any() or (pd.to_numeric(frame["close"]) <= 0).any():
            raise ValueError(f"invalid close values for {code}")
        providers.update(str(x) for x in frame["provider"].dropna().unique())
        frames.append(frame)

    output = pd.concat(frames, ignore_index=True)
    output["index_code"] = output["index_code"].astype(str).str.zfill(6)
    output = output.sort_values(["index_code", "date"]).reset_index(drop=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_csv, index=False)

    latest_dates = {
        code: output.loc[output["index_code"].eq(code), "date"].max().date().isoformat()
        for code in INDEX_CODES
    }
    latest = max(latest_dates.values())
    if any(value != latest for value in latest_dates.values()):
        raise ValueError(f"index latest dates are not aligned: {latest_dates}")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "PUBLIC_RAW_INPUT_READY_NO_PRIVATE_QUALIFICATION",
        "requested_as_of_date": as_of_date,
        "latest_market_date": latest,
        "start_date": start_date,
        "index_codes": list(INDEX_CODES),
        "row_count": int(len(output)),
        "rows_by_index": {
            code: int(output["index_code"].eq(code).sum()) for code in INDEX_CODES
        },
        "providers": sorted(providers),
        "csv_path": output_csv.as_posix(),
        "csv_sha256": _sha256(output_csv),
        "contains_model_output": False,
        "contains_private_evidence": False,
        "public_handoff_ready_grants_private_qualification": False,
        "automatic_trigger": False
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materialize public broad-index input for Market Regime V0.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--output-csv", default="data/reference/market_regime_v0_public_input_latest.csv")
    parser.add_argument("--output-manifest", default="reference/market_regime_v0_public_input_latest.json")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = build_market_regime_public_input(
        start_date=args.start_date,
        as_of_date=args.as_of_date,
        output_csv=Path(args.output_csv),
        output_manifest=Path(args.output_manifest),
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
