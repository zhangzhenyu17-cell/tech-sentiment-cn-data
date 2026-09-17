from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.capital_input_data import (
    SSE_MARGIN_SOURCE_ID,
    SSE_MARGIN_SOURCE_URL,
    SZSE_MARGIN_SOURCE_ID,
    SZSE_MARGIN_SOURCE_URL,
    qualify_financing_yuan,
)
from tech_sentiment.financing_materialization import materialize_financing_history
from tech_sentiment.index_price import fetch_index_history
from tech_sentiment.resumable_checkpoint import (
    load_csv_checkpoint,
    merge_csv_checkpoint,
)


SSE_COLUMNS = [
    "date",
    "sse_financing_balance",
    "sse_source_unit",
    "sse_source_identity",
    "sse_source_url",
]
SZSE_COLUMNS = [
    "date",
    "szse_financing_balance",
    "szse_source_unit",
    "szse_source_identity",
    "szse_source_url",
]


def _sha256_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _date_set(frame: pd.DataFrame) -> set[str]:
    if frame.empty:
        return set()
    return set(pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d"))


def _filter_dates(frame: pd.DataFrame, wanted: set[str]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    dates = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    return frame.loc[dates.isin(wanted)].copy().reset_index(drop=True)


def _concat_errors(parts: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [part for part in parts if part is not None and not part.empty]
    if not usable:
        return pd.DataFrame(columns=["date", "exchange", "error"])
    return pd.concat(usable, ignore_index=True, sort=False)[["date", "exchange", "error"]]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual-only official SSE/SZSE financing history materialization."
    )
    parser.add_argument("--start-date", default="2022-01-04")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-index-code", default="000906")
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--checkpoint-batch-size", type=int, default=20)
    parser.add_argument("--out-dir", default="output/financing_materialization")
    args = parser.parse_args()
    if args.checkpoint_batch_size <= 0:
        raise SystemExit("checkpoint batch size must be > 0")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    calendar = fetch_index_history(
        args.calendar_index_code,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if calendar.empty:
        raise SystemExit("trading calendar source returned no rows")
    dates = pd.DatetimeIndex(
        pd.to_datetime(calendar["date"], errors="raise")
    ).normalize().sort_values().unique()
    wanted_dates = {pd.Timestamp(value).strftime("%Y-%m-%d") for value in dates}

    checkpoint_root = Path(args.checkpoint_dir) if args.checkpoint_dir else None
    if checkpoint_root is not None:
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        sse_path = checkpoint_root / "sse_financing_success.csv"
        szse_path = checkpoint_root / "szse_financing_success.csv"
        sse_all = load_csv_checkpoint(
            sse_path,
            required_columns=SSE_COLUMNS,
            key_columns=["date"],
            expected_constants={
                "sse_source_unit": "CNY",
                "sse_source_identity": SSE_MARGIN_SOURCE_ID,
                "sse_source_url": SSE_MARGIN_SOURCE_URL,
            },
        )
        szse_all = load_csv_checkpoint(
            szse_path,
            required_columns=SZSE_COLUMNS,
            key_columns=["date"],
            expected_constants={
                "szse_source_unit": "CNY_100M",
                "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
                "szse_source_url": SZSE_MARGIN_SOURCE_URL,
            },
        )
    else:
        sse_path = szse_path = None
        sse_all = pd.DataFrame(columns=SSE_COLUMNS)
        szse_all = pd.DataFrame(columns=SZSE_COLUMNS)

    sse_seed = _filter_dates(sse_all, wanted_dates)
    szse_seed = _filter_dates(szse_all, wanted_dates)
    sse_reused = int(len(sse_seed))
    szse_reused = int(len(szse_seed))
    initial_sse_missing = wanted_dates - _date_set(sse_seed)
    initial_szse_missing = wanted_dates - _date_set(szse_seed)
    pending = sorted(initial_sse_missing | initial_szse_missing)
    error_parts: list[pd.DataFrame] = []

    for index in range(0, len(pending), args.checkpoint_batch_size):
        batch_strings = pending[index : index + args.checkpoint_batch_size]
        batch = [pd.Timestamp(value) for value in batch_strings]
        result = materialize_financing_history(batch)

        if len(result.raw):
            sse_rows = result.raw.loc[
                result.raw["sse_financing_balance"].notna(), SSE_COLUMNS
            ].copy()
            if len(sse_rows):
                sse_dates = pd.to_datetime(sse_rows["date"], errors="raise").dt.strftime("%Y-%m-%d")
                sse_rows = sse_rows.loc[sse_dates.isin(initial_sse_missing)].copy()
                if len(sse_rows):
                    if sse_path is not None:
                        sse_all = merge_csv_checkpoint(
                            sse_path,
                            sse_rows,
                            required_columns=SSE_COLUMNS,
                            key_columns=["date"],
                            expected_constants={
                                "sse_source_unit": "CNY",
                                "sse_source_identity": SSE_MARGIN_SOURCE_ID,
                                "sse_source_url": SSE_MARGIN_SOURCE_URL,
                            },
                        )
                    else:
                        sse_all = pd.concat([sse_all, sse_rows], ignore_index=True, sort=False)

            szse_rows = result.raw.loc[
                result.raw["szse_financing_balance"].notna(), SZSE_COLUMNS
            ].copy()
            if len(szse_rows):
                szse_dates = pd.to_datetime(szse_rows["date"], errors="raise").dt.strftime("%Y-%m-%d")
                szse_rows = szse_rows.loc[szse_dates.isin(initial_szse_missing)].copy()
                if len(szse_rows):
                    if szse_path is not None:
                        szse_all = merge_csv_checkpoint(
                            szse_path,
                            szse_rows,
                            required_columns=SZSE_COLUMNS,
                            key_columns=["date"],
                            expected_constants={
                                "szse_source_unit": "CNY_100M",
                                "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
                                "szse_source_url": SZSE_MARGIN_SOURCE_URL,
                            },
                        )
                    else:
                        szse_all = pd.concat([szse_all, szse_rows], ignore_index=True, sort=False)

        if len(result.errors):
            keep: list[bool] = []
            for _, row in result.errors.iterrows():
                exchange = str(row["exchange"])
                date = str(row["date"])
                if exchange == "SSE":
                    if date == "RANGE":
                        keep.append(bool(initial_sse_missing))
                    else:
                        keep.append(date in initial_sse_missing)
                elif exchange == "SZSE":
                    keep.append(date in initial_szse_missing)
                else:
                    keep.append(True)
            mask = pd.Series(keep, index=result.errors.index, dtype=bool)
            if mask.any():
                error_parts.append(result.errors.loc[mask].copy())

    sse = _filter_dates(sse_all, wanted_dates)
    szse = _filter_dates(szse_all, wanted_dates)
    if len(sse):
        sse["date"] = pd.to_datetime(sse["date"], errors="raise").dt.normalize()
    if len(szse):
        szse["date"] = pd.to_datetime(szse["date"], errors="raise").dt.normalize()
    raw = pd.DataFrame({"date": dates})
    raw = raw.merge(sse, on="date", how="left", validate="one_to_one")
    raw = raw.merge(szse, on="date", how="left", validate="one_to_one")
    raw["bilateral_complete"] = raw[
        ["sse_financing_balance", "szse_financing_balance"]
    ].notna().all(axis=1)
    complete = raw.loc[raw["bilateral_complete"]].copy()
    canonical, qualification = qualify_financing_yuan(complete)
    errors = _concat_errors(error_parts)

    source_query = {
        "calendar_start": str(pd.Timestamp(dates.min()).date()),
        "calendar_end": str(pd.Timestamp(dates.max()).date()),
        "target_days": int(len(dates)),
        "sse_source_identity": SSE_MARGIN_SOURCE_ID,
        "sse_source_url": SSE_MARGIN_SOURCE_URL,
        "sse_raw_unit": "CNY",
        "szse_source_identity": SZSE_MARGIN_SOURCE_ID,
        "szse_source_url": SZSE_MARGIN_SOURCE_URL,
        "szse_raw_unit": "CNY_100M",
        "canonical_unit": "CNY",
    }
    summary: dict[str, object] = {
        **source_query,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_query_identity": _sha256_payload(source_query),
        "bilateral_complete_days": int(raw["bilateral_complete"].sum()),
        "bilateral_coverage": float(raw["bilateral_complete"].mean()),
        "error_rows": int(len(errors)),
        "qualification_state": qualification.get("state", "DATA_INSUFFICIENT"),
        "median_scale_ratio": qualification.get("median_scale_ratio"),
        "role": "RESEARCH_INPUT",
        "included_in_capital_regime_composite": False,
        "no_unit_inference_from_anomaly": True,
        "resumable_checkpoint_enabled": checkpoint_root is not None,
        "checkpoint_batch_size": int(args.checkpoint_batch_size),
        "checkpoint_reused_sse_days": sse_reused,
        "checkpoint_reused_szse_days": szse_reused,
    }

    calendar.to_csv(out / "trading_calendar.csv", index=False)
    raw.to_csv(out / "financing_raw_aligned.csv", index=False)
    canonical.to_csv(out / "financing_canonical_cny.csv", index=False)
    errors.to_csv(out / "financing_errors.csv", index=False)
    (out / "financing_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
