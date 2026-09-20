from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


EVIDENCE_START = pd.Timestamp("2021-01-04")
SAMPLE_START = pd.Timestamp("2021-06-15")
END = pd.Timestamp("2021-12-31")


def _read_csv(path: Path, **kwargs: object) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, **kwargs)
    for column in ("date", "effective_start", "effective_end"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="raise").dt.normalize()
    if "symbol" in frame.columns:
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    return frame


def _read_receipt(root: Path) -> dict[str, object]:
    path = root / "receipt.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("public receipt must be a JSON object")
    return payload


def _calendar(root: Path, universe: str) -> pd.DatetimeIndex:
    frame = _read_csv(root / universe / "index_prices.csv")
    if "date" not in frame.columns:
        raise ValueError(f"{universe} index rail missing date")
    dates = pd.DatetimeIndex(frame["date"]).sort_values().unique()
    if not len(dates):
        raise ValueError(f"{universe} index rail is empty")
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare exact V4C-03 Phase A public PIT evidence inputs."
    )
    parser.add_argument("--phase-a-root", type=Path, required=True)
    parser.add_argument("--warmup-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    phase_a_root = args.phase_a_root.resolve()
    warmup_root = args.warmup_root.resolve()

    phase = _read_receipt(phase_a_root)
    warm = _read_receipt(warmup_root)
    if phase.get("status") != "PUBLIC_PHASE_A_ASSEMBLED":
        raise ValueError("unexpected Phase A receipt status")
    if phase.get("start_date") != "2021-06-15" or phase.get("end_date") != "2021-12-31":
        raise ValueError("Phase A receipt window mismatch")
    if warm.get("status") != "PUBLIC_PHASE_A_WARMUP_ASSEMBLED":
        raise ValueError("unexpected warm-up receipt status")
    if warm.get("start_date") != "2021-01-04" or warm.get("end_date") != "2021-06-14":
        raise ValueError("warm-up receipt window mismatch")
    if warm.get("sample_eligibility") is not False:
        raise ValueError("warm-up evidence must remain sample-ineligible")

    phase_star = _calendar(phase_a_root, "STAR50")
    phase_chi = _calendar(phase_a_root, "ChiNext50")
    warm_star = _calendar(warmup_root, "STAR50")
    warm_chi = _calendar(warmup_root, "ChiNext50")
    if not phase_star.equals(phase_chi):
        raise ValueError("Phase A STAR50/ChiNext50 calendars differ")
    if not warm_star.equals(warm_chi):
        raise ValueError("warm-up STAR50/ChiNext50 calendars differ")
    if set(warm_star).intersection(set(phase_star)):
        raise ValueError("warm-up/sample calendar overlap")
    calendar = pd.DatetimeIndex(sorted(set(warm_star) | set(phase_star)))
    if calendar.min() != EVIDENCE_START or calendar.max() != END:
        raise ValueError("combined evidence calendar does not span frozen window")
    if SAMPLE_START not in calendar:
        raise ValueError("sample start is absent from evidence calendar")

    scope_path = phase_a_root / "pit_symbol_scope" / "capital_pit_symbols.csv"
    scope = _read_csv(scope_path, dtype={"symbol": str})
    if "symbol" not in scope.columns:
        raise ValueError("Phase A symbol scope missing symbol")
    symbols = sorted(set(scope["symbol"].dropna().astype(str).str.zfill(6)))
    if not symbols:
        raise ValueError("Phase A symbol scope is empty")
    symbol_frame = pd.DataFrame({"symbol": symbols})

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    symbol_frame.to_csv(out / "symbols.csv", index=False)
    pd.DataFrame({"date": calendar}).to_csv(
        out / "trading_calendar.csv", index=False, date_format="%Y-%m-%d"
    )

    market = out / "market"
    for universe in ("STAR50", "ChiNext50"):
        target = market / universe
        target.mkdir(parents=True, exist_ok=True)
        for name in ("prices.csv", "universe_point_in_time.csv", "index_prices.csv"):
            frame = _read_csv(
                phase_a_root / universe / name,
                dtype={"symbol": str} if name != "index_prices.csv" else None,
            )
            frame.to_csv(target / name, index=False, date_format="%Y-%m-%d")

    receipt = {
        "schema_version": "v4c03-phase-a-evidence-inputs-v1",
        "status": "V4C03_PHASE_A_EVIDENCE_INPUTS_PREPARED",
        "evidence_start": str(EVIDENCE_START.date()),
        "sample_start": str(SAMPLE_START.date()),
        "end_date": str(END.date()),
        "sample_eligible_before_sample_start": False,
        "trading_days": int(len(calendar)),
        "symbols": int(len(symbols)),
        "phase_a_source_commit": phase.get("source_commit"),
        "warmup_source_commit": warm.get("source_commit"),
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "forward_result_computation_run": False,
        "production_or_trading_authority_changed": False,
    }
    (out / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
