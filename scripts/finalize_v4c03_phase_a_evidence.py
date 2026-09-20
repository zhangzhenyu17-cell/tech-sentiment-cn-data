from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pandas as pd


EVIDENCE_START = "2021-01-04"
SAMPLE_START = "2021-06-15"
END_DATE = "2021-12-31"

DERIVED_REQUIRED = (
    "fundamental_state_evidence.csv",
    "earnings_direction_evidence.csv",
    "trailing_valuation_rail.csv",
    "major_negative_review.csv",
    "pit_evidence_extended.csv",
    "derived_pit_materialization_manifest.json",
)
CAPITAL_REQUIRED = (
    "etf_shares.csv",
    "etf_share_coverage.csv",
    "sse_szse_a_share_turnover.csv",
    "capital_summary.json",
)
MARKET_REQUIRED = (
    "STAR50/prices.csv",
    "STAR50/universe_point_in_time.csv",
    "STAR50/index_prices.csv",
    "ChiNext50/prices.csv",
    "ChiNext50/universe_point_in_time.csv",
    "ChiNext50/index_prices.csv",
)

FORBIDDEN_OUTCOME_EXACT = {
    "mae",
    "mfe",
    "max_drawdown",
    "mae_60d",
    "mfe_60d",
    "max_drawdown_60d",
    "false_bottom",
    "false_top",
    "recovery_latency_bars",
    "opportunity_cost_60d",
}


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _assert_no_outcomes(root: Path) -> None:
    for path in sorted(root.rglob("*.csv")):
        try:
            header = pd.read_csv(path, nrows=0)
        except pd.errors.EmptyDataError:
            continue
        bad = [
            str(column)
            for column in header.columns
            if str(column).startswith(("ret_", "fwd_"))
            or str(column).lower() in FORBIDDEN_OUTCOME_EXACT
        ]
        if bad:
            raise ValueError(f"forbidden outcome columns in {path}: {bad}")


def _copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _file_rows(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha(path),
                "bytes": int(path.stat().st_size),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize public-only V4C-03 Phase A PIT evidence materialization."
    )
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--derived-dir", type=Path, required=True)
    parser.add_argument("--capital-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    inputs = args.inputs_dir.resolve()
    derived = args.derived_dir.resolve()
    capital = args.capital_dir.resolve()

    input_receipt = _read_json(inputs / "receipt.json")
    if input_receipt.get("status") != "V4C03_PHASE_A_EVIDENCE_INPUTS_PREPARED":
        raise ValueError("V4C-03 evidence-input receipt status mismatch")
    if input_receipt.get("evidence_start") != EVIDENCE_START:
        raise ValueError("V4C-03 evidence-input start mismatch")
    if input_receipt.get("sample_start") != SAMPLE_START:
        raise ValueError("V4C-03 evidence-input sample start mismatch")
    if input_receipt.get("end_date") != END_DATE:
        raise ValueError("V4C-03 evidence-input end mismatch")
    if input_receipt.get("sample_eligible_before_sample_start") is not False:
        raise ValueError("pre-sample evidence must remain sample-ineligible")

    for relative in DERIVED_REQUIRED:
        if not (derived / relative).is_file():
            raise FileNotFoundError(f"derived evidence file missing: {relative}")
    for relative in CAPITAL_REQUIRED:
        if not (capital / relative).is_file():
            raise FileNotFoundError(f"capital evidence file missing: {relative}")
    for relative in MARKET_REQUIRED:
        if not (inputs / "market" / relative).is_file():
            raise FileNotFoundError(f"sample market file missing: {relative}")

    derived_manifest = _read_json(derived / "derived_pit_materialization_manifest.json")
    if derived_manifest.get("status") != "PUBLIC_PIT_MATERIALIZATION_COMPLETED":
        raise ValueError("derived PIT materialization status mismatch")
    if derived_manifest.get("start_date") != EVIDENCE_START:
        raise ValueError("derived PIT start mismatch")
    if derived_manifest.get("end_date") != END_DATE:
        raise ValueError("derived PIT end mismatch")
    for key in (
        "future_prices_or_returns_used_for_fundamental_or_earnings",
        "predictive_research_run",
        "parameter_search_run",
        "holdout_run",
        "production_run",
    ):
        if derived_manifest.get(key) is not False:
            raise ValueError(f"derived PIT boundary drift: {key}")

    capital_summary = _read_json(capital / "capital_summary.json")
    if capital_summary.get("status") != "V4C03_PUBLIC_CAPITAL_MATERIALIZED":
        raise ValueError("V4C-03 capital status mismatch")
    if capital_summary.get("start_date") != EVIDENCE_START:
        raise ValueError("V4C-03 capital start mismatch")
    if capital_summary.get("end_date") != END_DATE:
        raise ValueError("V4C-03 capital end mismatch")
    for key in (
        "future_prices_or_returns_used",
        "predictive_research_run",
        "parameter_search_run",
        "production_or_trading_authority_changed",
    ):
        if capital_summary.get(key) is not False:
            raise ValueError(f"capital boundary drift: {key}")

    shares = pd.read_csv(capital / "etf_shares.csv", dtype={"fund_code": str})
    if "fund_code" not in shares.columns:
        raise ValueError("capital ETF rail missing fund_code")
    funds = set(
        shares["fund_code"]
        .dropna()
        .astype(str)
        .str.extract(r"(\d+)", expand=False)
        .str.zfill(6)
    )
    required_funds = {"588000", "159915"}
    if not required_funds.issubset(funds):
        raise ValueError(
            f"capital ETF rail missing frozen funds: {sorted(required_funds - funds)}"
        )

    turnover = pd.read_csv(capital / "sse_szse_a_share_turnover.csv")
    if not {"date", "amount"}.issubset(turnover.columns):
        raise ValueError("broad-market turnover missing date/amount")
    if len(turnover):
        turnover_dates = pd.to_datetime(turnover["date"], errors="raise").dt.normalize()
        if turnover_dates.duplicated().any():
            raise ValueError("broad-market turnover contains duplicate dates")

    out = args.out_dir.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    _copy_tree(inputs / "market", out / "market")
    _copy_tree(derived, out / "derived" / "pit_evidence_materialization")
    _copy_tree(capital, out / "capital_input_qualification")
    shutil.copy2(inputs / "symbols.csv", out / "symbols.csv")
    shutil.copy2(inputs / "trading_calendar.csv", out / "trading_calendar.csv")
    shutil.copy2(inputs / "receipt.json", out / "input_receipt.json")

    _assert_no_outcomes(out)

    receipt = {
        "schema_version": "v4c03-phase-a-public-evidence-receipt-v1",
        "status": "V4C03_PHASE_A_PUBLIC_EVIDENCE_MATERIALIZED_OUTCOME_BLIND",
        "source_commit": args.source_commit,
        "evidence_start": EVIDENCE_START,
        "sample_start": SAMPLE_START,
        "end_date": END_DATE,
        "pre_sample_evidence_sample_eligible": False,
        "derived_readiness": {
            "source_states": derived_manifest.get("source_states"),
            "earnings_direction_readiness_state": derived_manifest.get(
                "earnings_direction_readiness_state"
            ),
            "major_negative_event_exclusion_complete": derived_manifest.get(
                "major_negative_event_exclusion_complete"
            ),
            "valuation_coverage": derived_manifest.get("valuation_coverage"),
        },
        "capital_readiness": {
            "funds": capital_summary.get("funds"),
            "turnover_complete_days": capital_summary.get("turnover_complete_days"),
            "turnover_expected_days": capital_summary.get("turnover_expected_days"),
            "turnover_error_rows": capital_summary.get("turnover_error_rows"),
            "sse_etf_error_rows": capital_summary.get("sse_etf_error_rows"),
            "szse_etf_error_chunks": capital_summary.get("szse_etf_error_chunks"),
        },
        "public_only": True,
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "new_context_outcome_read": False,
        "new_context_forward_outcomes_read": False,
        "outcome_columns_materialized": False,
        "real_outcome_study_executed": False,
        "provider_refetch_for_existing_phase_a_or_warmup": False,
        "parameter_search_run": False,
        "feature_search_run": False,
        "threshold_search_run": False,
        "ml_run": False,
        "context_or_cause_semantics_changed": False,
        "universe_changed": False,
        "evidence_qualification_semantics_changed": False,
        "score_mapping_changed": False,
        "production_authority_changed": False,
        "trading_authority_changed": False,
    }
    receipt["files"] = _file_rows(out)
    receipt_path = out / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
