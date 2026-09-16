from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path

import pandas as pd


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"


def _rows(result) -> list[list[str]]:
    rows: list[list[str]] = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time research probe for BaoStock historical ST/trading-status fields."
    )
    parser.add_argument("--codes", required=True, help="Comma-separated BaoStock codes, e.g. sh.600276,sz.300558")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    try:
        import baostock as bs
    except ImportError as exc:
        raise SystemExit("install the sector-data extra: pip install -e '.[sector-data]'") from exc

    codes = [value.strip() for value in args.codes.split(",") if value.strip()]
    if not codes:
        raise SystemExit("no codes supplied")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed: {login.error_msg}")

    history_parts: list[pd.DataFrame] = []
    basic_parts: list[pd.DataFrame] = []
    try:
        for code in codes:
            result = bs.query_history_k_data_plus(
                code,
                FIELDS,
                start_date=args.start,
                end_date=args.end,
                frequency="d",
                adjustflag="3",  # raw/unadjusted prices for exchange-rule auditing
            )
            data = _rows(result)
            history_parts.append(pd.DataFrame(data, columns=result.fields))

            basic = bs.query_stock_basic(code=code)
            basic_data = _rows(basic)
            basic_parts.append(pd.DataFrame(basic_data, columns=basic.fields))
    finally:
        bs.logout()

    history = pd.concat(history_parts, ignore_index=True) if history_parts else pd.DataFrame()
    basic = pd.concat(basic_parts, ignore_index=True) if basic_parts else pd.DataFrame()
    history.to_csv(out / "baostock_history_status.csv", index=False)
    basic.to_csv(out / "baostock_stock_basic.csv", index=False)

    manifest = {
        "provider": "BaoStock",
        "provider_version": version("baostock"),
        "query_fields": FIELDS.split(","),
        "adjustflag": "3",
        "frequency": "d",
        "start_date": args.start,
        "end_date": args.end,
        "codes": codes,
        "history_rows": int(len(history)),
        "basic_rows": int(len(basic)),
        "qualification": "CANDIDATE_STATUS_SOURCE_ONLY",
        "warning": (
            "isST/tradestatus do not by themselves establish IPO, relisting, "
            "delisting-transition, or other special no-limit-day eligibility"
        ),
    }
    (out / "baostock_probe_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
