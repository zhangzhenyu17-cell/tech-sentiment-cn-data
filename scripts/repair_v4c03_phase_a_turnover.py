from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Callable

import pandas as pd

from tech_sentiment.capital_input_data import (
    ExchangeTurnoverFetchResult,
    fetch_sse_szse_a_share_turnover_history,
)


EXPECTED_START = pd.Timestamp("2021-01-04")
EXPECTED_END = pd.Timestamp("2021-12-31")
EXPECTED_TRADING_DAYS = 243

PINNED_UNCHANGED = {
    "etf_shares.csv": "c921b370c59661d4697c03fc26d0d0fe23c4c08f9d55068dbf0bd3f75ea1466a",
    "etf_share_coverage.csv": "85b557f844c052ca1f0510d88d1205a59f97301f0651b3a9ee63316f5bda899a",
    "sse_etf_share_errors.csv": "6dbce147db23918cd22b956e3c09912bd6e267d98da1e1cf3a02e70846e289b2",
    "szse_etf_share_errors.csv": "1ecea44960b8f1b46e6f5a0ece7ed60f5ca9301969171b13732e475afec809d2",
}


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _calendar(root: Path) -> pd.DatetimeIndex:
    path = root / "trading_calendar.csv"
    frame = pd.read_csv(path)
    if "date" not in frame.columns:
        raise ValueError("base public evidence trading calendar missing date")
    dates = (
        pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if len(dates) != EXPECTED_TRADING_DAYS:
        raise ValueError(
            f"unexpected V4C-03 trading-day count: {len(dates)} != {EXPECTED_TRADING_DAYS}"
        )
    if dates.min() != EXPECTED_START or dates.max() != EXPECTED_END:
        raise ValueError("V4C-03 turnover repair calendar boundary mismatch")
    return dates


def _assert_exact_calendar(frame: pd.DataFrame, dates: pd.DatetimeIndex, *, label: str) -> None:
    if "date" not in frame.columns:
        raise ValueError(f"{label} missing date")
    observed = (
        pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not observed.equals(dates):
        missing = dates.difference(observed)
        extra = observed.difference(dates)
        raise ValueError(
            f"{label} calendar mismatch; missing={list(map(str, missing[:5]))}, "
            f"extra={list(map(str, extra[:5]))}"
        )


def repair_turnover(
    *,
    base_evidence_dir: Path,
    out_dir: Path,
    source_commit: str,
    fetcher: Callable[..., ExchangeTurnoverFetchResult] = fetch_sse_szse_a_share_turnover_history,
) -> dict[str, object]:
    base = base_evidence_dir.resolve()
    capital = base / "capital_input_qualification"
    if not capital.is_dir():
        raise FileNotFoundError(capital)

    base_receipt = _read_json(base / "receipt.json")
    if base_receipt.get("status") != "V4C03_PHASE_A_PUBLIC_EVIDENCE_MATERIALIZED_OUTCOME_BLIND":
        raise ValueError("base public evidence receipt status mismatch")
    if base_receipt.get("source_commit") != "c2852749b8fcdcaab6ac3b76ead89d6f5a1baad1":
        raise ValueError("base public evidence source commit mismatch")
    for key in (
        "new_context_outcome_read",
        "new_context_forward_outcomes_read",
        "outcome_columns_materialized",
        "real_outcome_study_executed",
        "parameter_search_run",
        "feature_search_run",
        "threshold_search_run",
        "ml_run",
        "context_or_cause_semantics_changed",
        "universe_changed",
        "evidence_qualification_semantics_changed",
        "score_mapping_changed",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        if base_receipt.get(key) is not False:
            raise ValueError(f"base public evidence boundary drift: {key}")

    for name, expected in PINNED_UNCHANGED.items():
        path = capital / name
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = _sha(path)
        if actual != expected:
            raise ValueError(f"pinned unchanged capital file SHA mismatch: {name}")

    dates = _calendar(base)
    fetched = fetcher(
        trading_dates=dates,
        sleep_seconds=0.05,
        retry_attempts=3,
        retry_backoff_seconds=0.6,
    )
    if not fetched.errors.empty:
        raise ValueError(
            "turnover repair remains incomplete: "
            + json.dumps(
                fetched.errors.head(20).to_dict("records"),
                ensure_ascii=False,
                default=str,
            )
        )
    _assert_exact_calendar(fetched.sse, dates, label="repaired SSE turnover")
    _assert_exact_calendar(fetched.szse, dates, label="repaired SZSE turnover")
    _assert_exact_calendar(fetched.combined, dates, label="repaired combined turnover")
    amount = pd.to_numeric(fetched.combined["amount"], errors="coerce")
    if amount.isna().any() or (amount <= 0).any():
        raise ValueError("repaired combined turnover contains invalid canonical amount")

    out = out_dir.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in PINNED_UNCHANGED:
        shutil.copy2(capital / name, out / name)
    shutil.copy2(base / "trading_calendar.csv", out / "trading_calendar.csv")

    fetched.sse.to_csv(out / "sse_a_share_turnover.csv", index=False, date_format="%Y-%m-%d")
    fetched.szse.to_csv(out / "szse_a_share_turnover.csv", index=False, date_format="%Y-%m-%d")
    fetched.combined.to_csv(
        out / "sse_szse_a_share_turnover.csv",
        index=False,
        date_format="%Y-%m-%d",
    )
    fetched.errors.to_csv(out / "sse_szse_turnover_errors.csv", index=False)

    base_summary = _read_json(capital / "capital_summary.json")
    summary = dict(base_summary)
    summary.update(
        {
            "status": "V4C03_PUBLIC_CAPITAL_MATERIALIZED",
            "source_commit": source_commit,
            "turnover_complete_days": int(len(fetched.combined)),
            "turnover_expected_days": int(len(dates)),
            "turnover_error_rows": 0,
            "turnover_repair": {
                "status": "EXACT_CALENDAR_REPAIRED",
                "base_public_evidence_bundle_identity": (
                    "6ef568d8c84abd98214eaafab028a62da2fceda46b595461e58f06089b05342d"
                ),
                "sse_historical_sql_id": "COMMON_SSE_SJ_GPSJ_CJGK_DAYCJGK_C",
                "sse_historical_cutoff_inclusive": "2021-12-24",
                "provider_refetch_scope": "TURNOVER_ONLY",
                "etf_share_provider_refetch": False,
                "exact_calendar_equality": True,
            },
            "future_prices_or_returns_used": False,
            "predictive_research_run": False,
            "parameter_search_run": False,
            "production_or_trading_authority_changed": False,
        }
    )
    (out / "capital_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    receipt = {
        "schema_version": "v4c03-phase-a-turnover-repair-v1",
        "status": "V4C03_PUBLIC_CAPITAL_TURNOVER_REPAIRED",
        "source_commit": source_commit,
        "base_public_evidence_bundle_identity": (
            "6ef568d8c84abd98214eaafab028a62da2fceda46b595461e58f06089b05342d"
        ),
        "start_date": str(dates.min().date()),
        "end_date": str(dates.max().date()),
        "turnover_complete_days": int(len(fetched.combined)),
        "turnover_expected_days": int(len(dates)),
        "turnover_error_rows": 0,
        "provider_refetch_scope": "TURNOVER_ONLY",
        "etf_share_provider_refetch": False,
        "issuer_provider_refetch": False,
        "fundamental_provider_refetch": False,
        "price_provider_refetch": False,
        "policy_provider_refetch": False,
        "no_interpolation": True,
        "no_forward_fill": True,
        "new_context_outcome_read": False,
        "new_context_forward_outcomes_read": False,
        "outcome_columns_materialized": False,
        "real_outcome_study_executed": False,
        "parameter_search_run": False,
        "feature_search_run": False,
        "threshold_search_run": False,
        "ml_run": False,
        "context_or_cause_semantics_changed": False,
        "universe_changed": False,
        "evidence_qualification_semantics_changed": False,
        "production_authority_changed": False,
        "trading_authority_changed": False,
        "pinned_unchanged_sha256": {
            name: _sha(out / name) for name in PINNED_UNCHANGED
        },
    }
    (out / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair only V4C-03 Phase A SSE+SZSE turnover coverage."
    )
    parser.add_argument("--base-evidence-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = repair_turnover(
        base_evidence_dir=args.base_evidence_dir,
        out_dir=args.out_dir,
        source_commit=args.source_commit,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
