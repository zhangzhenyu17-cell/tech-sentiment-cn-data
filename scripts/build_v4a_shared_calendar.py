from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.index_price import fetch_index_history


SCHEMA_VERSION = "v4a-shared-calendar-v1"


def _calendar_identity(frame: pd.DataFrame) -> str:
    dates = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    payload = "\n".join(value.strftime("%Y-%m-%d") for value in dates)
    return sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the one shared V4-A real trading calendar.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--calendar-index-code", default="000688")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    start = pd.Timestamp(args.start_date).normalize()
    end = pd.Timestamp(args.end_date).normalize()
    if end < start:
        raise SystemExit("end-date must not precede start-date")

    frame = fetch_index_history(
        args.calendar_index_code,
        start_date=str(start.date()),
        end_date=str(end.date()),
    )
    if frame.empty or "date" not in frame.columns:
        raise SystemExit("trading calendar source returned no usable rows")
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out = out[out["date"].between(start, end)].sort_values("date").drop_duplicates("date", keep="last")
    if out.empty:
        raise SystemExit("trading calendar contains no target-window dates")

    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    out.to_csv(output / "trading_calendar.csv", index=False)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": args.source_commit,
        "calendar_index_code": str(args.calendar_index_code),
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "trading_days": int(len(out)),
        "calendar_identity": _calendar_identity(out),
        "interpolation": False,
        "forward_fill": False,
        "backfill": False,
        "production_or_model_output": False,
        "predictive_research_run": False,
    }
    (output / "trading_calendar_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
