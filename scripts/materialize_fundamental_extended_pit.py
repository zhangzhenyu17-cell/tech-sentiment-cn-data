from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.cninfo_resilient_download import (
    download_cninfo_document_resilient,
)
import tech_sentiment.extended_filing_materialization as extended_materialization


def _symbols(path: Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("symbols CSV must contain symbol")
    values = sorted(
        {
            "".join(ch for ch in str(value) if ch.isdigit()).zfill(6)
            for value in frame["symbol"].dropna()
        }
    )
    if not values:
        raise ValueError("symbols CSV resolved no symbols")
    return values


def _trading_dates(path: Path) -> list[str]:
    frame = pd.read_csv(path, dtype=str)
    if "date" not in frame.columns:
        raise ValueError("trading calendar CSV must contain date")
    values = sorted(
        {
            str(pd.Timestamp(value).date())
            for value in frame["date"].dropna()
        }
    )
    if not values:
        raise ValueError("trading calendar resolved no dates")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize outcome-blind extended official-filing raw PIT facts. "
            "This script does not qualify evidence or read outcomes."
        )
    )
    parser.add_argument("--symbols-csv", type=Path, required=True)
    parser.add_argument("--trading-calendar-csv", type=Path, required=True)
    parser.add_argument("--target-start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--warmup-years", type=int, default=2)
    parser.add_argument("--checkpoint-source-commit")
    parser.add_argument("--legacy-checkpoint-dir", type=Path)
    parser.add_argument(
        "--legacy-query-checkpoint-source-commit",
        action="append",
        default=[],
        dest="legacy_query_checkpoint_source_commits",
    )
    parser.add_argument("--progress-checkpoint-source-commit")
    parser.add_argument("--hard-failure-circuit-breaker-threshold", type=int, default=3)
    parser.add_argument("--max-financial-documents-per-symbol", type=int)
    parser.add_argument("--fail-on-hard-errors", action="store_true")
    args = parser.parse_args()

    # Extended PIT runs already use the canonical official downloader. Install a
    # transport-only compatibility adapter for this historical backfill entrypoint
    # so bounded transient failures can continue through the existing same-provider
    # CNINFO HTTPS session/browser transports. Evidence identity and parser rules
    # remain unchanged.
    extended_materialization.download_official_document = (
        download_cninfo_document_resilient
    )

    result = extended_materialization.materialize_extended_filing_facts(
        _symbols(args.symbols_csv),
        target_start_date=args.target_start_date,
        end_date=args.end_date,
        trading_dates=_trading_dates(args.trading_calendar_csv),
        source_commit=args.source_commit,
        checkpoint_dir=args.checkpoint_dir,
        warmup_years=args.warmup_years,
        checkpoint_source_commit=args.checkpoint_source_commit,
        legacy_checkpoint_dir=args.legacy_checkpoint_dir,
        legacy_query_checkpoint_source_commits=args.legacy_query_checkpoint_source_commits,
        progress_checkpoint_source_commit=args.progress_checkpoint_source_commit,
        hard_failure_circuit_breaker_threshold=args.hard_failure_circuit_breaker_threshold,
        max_financial_documents_per_symbol=args.max_financial_documents_per_symbol,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.facts.to_csv(
        args.output_dir / "extended_filing_facts.csv",
        index=False,
        lineterminator="\n",
    )
    result.coverage.to_csv(
        args.output_dir / "extended_filing_coverage.csv",
        index=False,
        lineterminator="\n",
    )
    result.errors.to_csv(
        args.output_dir / "extended_filing_errors.csv",
        index=False,
        lineterminator="\n",
    )
    (args.output_dir / "extended_filing_summary.json").write_text(
        json.dumps(
            result.summary,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.fail_on_hard_errors and (
        int(result.summary.get("hard_failure_rows") or 0) > 0
        or bool(result.summary.get("circuit_breaker_tripped"))
    ):
        raise SystemExit("extended PIT materialization has hard failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
