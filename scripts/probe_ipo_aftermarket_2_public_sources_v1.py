from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.filing_materialization import materialize_versioned_filing_facts
from tech_sentiment.fundamental_pit_state import materialize_fundamental_state_evidence
from tech_sentiment.ipo_aftermarket_2_public_probe_v1 import (
    CSRC_TAXONOMY_LABEL,
    deterministic_filing_probe_sample,
    deterministic_symbol_probe_sample,
    normalize_industry_change_probe,
    normalize_industry_pe_probe,
    retry_call,
    select_probe_trade_dates,
    write_json,
)


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, **kwargs)


def _trading_calendar(ak, *, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    raw = retry_call(lambda: ak.tool_trade_date_hist_sina())
    col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    dates = pd.to_datetime(raw[col], errors="coerce").dropna().dt.normalize()
    dates = dates[dates.between(start, end)]
    if dates.empty:
        raise ValueError("public trading calendar probe returned no dates")
    return pd.DatetimeIndex(dates).sort_values().unique()


def _industry_change_probe(ak, metadata: pd.DataFrame, *, as_of: pd.Timestamp) -> list[dict]:
    samples = deterministic_symbol_probe_sample(metadata, per_board=2)
    by_symbol = metadata.copy()
    by_symbol["symbol"] = by_symbol["symbol"].astype(str).str.zfill(6)
    by_symbol["listing_date"] = pd.to_datetime(
        by_symbol["listing_date"], errors="raise"
    ).dt.normalize()
    by_symbol = by_symbol.set_index("symbol")

    rows: list[dict] = []
    for symbol in samples:
        listing = pd.Timestamp(by_symbol.loc[symbol, "listing_date"]).normalize()
        try:
            raw = retry_call(
                lambda s=symbol, st=listing: ak.stock_industry_change_cninfo(
                    symbol=s,
                    start_date=st.strftime("%Y%m%d"),
                    end_date=as_of.strftime("%Y%m%d"),
                )
            )
            item = normalize_industry_change_probe(raw, symbol=symbol)
            item.update(
                {
                    "listing_date": str(listing.date()),
                    "status": "QUERY_OK",
                    "error": None,
                }
            )
        except Exception as exc:
            item = {
                "symbol": symbol,
                "listing_date": str(listing.date()),
                "status": "QUERY_ERROR",
                "row_count": 0,
                "csrc_row_count": 0,
                "required_columns_present": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(item)
    return rows


def _industry_pe_probe(ak, benchmark: pd.DataFrame, *, as_of: pd.Timestamp) -> list[dict]:
    rows: list[dict] = []
    for date in select_probe_trade_dates(benchmark, as_of=as_of):
        try:
            raw = retry_call(
                lambda d=date: ak.stock_industry_pe_ratio_cninfo(
                    symbol=CSRC_TAXONOMY_LABEL,
                    date=d.strftime("%Y%m%d"),
                )
            )
            item = normalize_industry_pe_probe(raw, requested_date=date)
            item.update({"status": "QUERY_OK", "error": None})
        except Exception as exc:
            item = {
                "requested_date": str(date.date()),
                "status": "QUERY_ERROR",
                "row_count": 0,
                "exact_date_row_count": 0,
                "positive_static_median_count": 0,
                "industry_code_count": 0,
                "required_columns_present": False,
                "returned_dates": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(item)
    return rows


def _filing_probe(
    ak,
    metadata: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    source_commit: str,
    out_dir: Path,
) -> list[dict]:
    samples = deterministic_filing_probe_sample(metadata)
    by_symbol = metadata.copy()
    by_symbol["symbol"] = by_symbol["symbol"].astype(str).str.zfill(6)
    by_symbol["listing_date"] = pd.to_datetime(
        by_symbol["listing_date"], errors="raise"
    ).dt.normalize()
    by_symbol = by_symbol.set_index("symbol")

    # Use the official trading calendar independently of the base benchmark
    # history so a warmup filing before 2019 does not disappear only because
    # the IPO V1 benchmark rail starts later.
    calendar = _trading_calendar(
        ak,
        start=pd.Timestamp("2016-01-01"),
        end=as_of,
    )

    rows: list[dict] = []
    for symbol in samples:
        listing = pd.Timestamp(by_symbol.loc[symbol, "listing_date"]).normalize()
        target_end = min(as_of, listing + pd.Timedelta(days=180))
        checkpoint = out_dir / "checkpoints" / symbol
        try:
            filings = materialize_versioned_filing_facts(
                [symbol],
                target_start_date=listing,
                end_date=target_end,
                trading_dates=calendar,
                source_commit=source_commit,
                checkpoint_dir=checkpoint,
                warmup_years=2,
            )
            state = materialize_fundamental_state_evidence(
                filings.facts,
                target_start_date=listing,
                target_end_date=target_end,
            )
            qualified = (
                int(
                    state.evidence["availability_state"]
                    .astype(str)
                    .eq("HISTORICAL_RECONSTRUCTABLE")
                    .sum()
                )
                if len(state.evidence)
                else 0
            )
            first_qualified = None
            if qualified:
                q = state.evidence.loc[
                    state.evidence["availability_state"]
                    .astype(str)
                    .eq("HISTORICAL_RECONSTRUCTABLE")
                ].copy()
                first_qualified = str(
                    pd.to_datetime(q["evidence_available_date"]).min().date()
                )
            rows.append(
                {
                    "symbol": symbol,
                    "listing_date": str(listing.date()),
                    "probe_end": str(target_end.date()),
                    "status": "QUERY_OK",
                    "filing_fact_rows": int(len(filings.facts)),
                    "filing_error_rows": int(len(filings.errors)),
                    "fundamental_state_rows": int(len(state.evidence)),
                    "qualified_fundamental_state_rows": qualified,
                    "first_qualified_state_available_date": first_qualified,
                    "fundamental_readiness_state": str(
                        state.summary.get("readiness_state")
                    ),
                    "error": None,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "symbol": symbol,
                    "listing_date": str(listing.date()),
                    "probe_end": str(target_end.date()),
                    "status": "QUERY_ERROR",
                    "filing_fact_rows": 0,
                    "filing_error_rows": 0,
                    "fundamental_state_rows": 0,
                    "qualified_fundamental_state_rows": 0,
                    "first_qualified_state_available_date": None,
                    "fundamental_readiness_state": "DATA_INSUFFICIENT",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return rows


def _summarize(
    *,
    industry_change: list[dict],
    industry_pe: list[dict],
    filing: list[dict],
    as_of: pd.Timestamp,
    source_commit: str,
) -> dict:
    change_ok = [
        row
        for row in industry_change
        if row.get("status") == "QUERY_OK"
        and row.get("required_columns_present")
        and int(row.get("csrc_row_count") or 0) > 0
    ]
    pe_exact = [
        row
        for row in industry_pe
        if row.get("status") == "QUERY_OK"
        and row.get("required_columns_present")
        and int(row.get("exact_date_row_count") or 0) > 0
        and int(row.get("positive_static_median_count") or 0) > 0
    ]
    historical_pe = [
        row for row in pe_exact if str(row["requested_date"]) < "2026-01-01"
    ]
    filing_ok = [
        row
        for row in filing
        if row.get("status") == "QUERY_OK"
        and int(row.get("filing_fact_rows") or 0) > 0
    ]
    state_ok = [
        row
        for row in filing
        if row.get("status") == "QUERY_OK"
        and int(row.get("qualified_fundamental_state_rows") or 0) > 0
    ]

    if len(historical_pe) >= 2:
        pe_state = "HISTORICAL_EXACT_DATE_REACHABLE_IN_PROBE"
    elif pe_exact:
        pe_state = "RECENT_ONLY_OR_PARTIAL_HISTORICAL_REACHABILITY"
    else:
        pe_state = "DATA_INSUFFICIENT_OR_SOURCE_UNREACHABLE"

    if len(change_ok) == len(industry_change) and change_ok:
        change_state = "REACHABLE"
    elif change_ok:
        change_state = "PARTIAL_COVERAGE"
    else:
        change_state = "DATA_INSUFFICIENT_OR_SOURCE_UNREACHABLE"

    if state_ok:
        filing_state = "PIT_STATE_PRESENT_FOR_AT_LEAST_ONE_PUBLIC_IPO_SAMPLE"
    elif filing_ok:
        filing_state = "FILING_FACTS_REACHABLE_BUT_EARLY_LIFE_STATE_DATA_INSUFFICIENT"
    else:
        filing_state = "DATA_INSUFFICIENT_OR_SOURCE_UNREACHABLE"

    return {
        "schema_version": "ipo-aftermarket-2-public-source-probe-v1",
        "status": "PUBLIC_SOURCE_PROBE_COMPLETE",
        "as_of": str(as_of.date()),
        "source_commit": source_commit,
        "industry_change_state": change_state,
        "industry_pe_state": pe_state,
        "filing_and_fundamental_state": filing_state,
        "industry_change_samples": len(industry_change),
        "industry_change_csrc_successes": len(change_ok),
        "industry_pe_dates_probed": len(industry_pe),
        "industry_pe_exact_date_successes": len(pe_exact),
        "industry_pe_pre_2026_exact_date_successes": len(historical_pe),
        "filing_samples": len(filing),
        "filing_fact_successes": len(filing_ok),
        "fundamental_state_successes": len(state_ok),
        "full_materialization_recommended": (
            change_state in {"REACHABLE", "PARTIAL_COVERAGE"}
            and pe_state == "HISTORICAL_EXACT_DATE_REACHABLE_IN_PROBE"
            and bool(filing_ok)
        ),
        "private_event_identities_used": False,
        "forward_outcomes_used": False,
        "research_eligibility_computed": False,
        "production_or_trading_authority_changed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bundle-dir", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    import akshare as ak

    as_of = pd.Timestamp(args.as_of).normalize()
    metadata = _read_csv(
        args.bundle_dir / "ipo_metadata.csv",
        dtype={"symbol": str},
    )
    benchmark = _read_csv(args.bundle_dir / "benchmark_prices.csv")

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    industry_change = _industry_change_probe(ak, metadata, as_of=as_of)
    industry_pe = _industry_pe_probe(ak, benchmark, as_of=as_of)
    filing = _filing_probe(
        ak,
        metadata,
        as_of=as_of,
        source_commit=args.source_commit,
        out_dir=out,
    )
    summary = _summarize(
        industry_change=industry_change,
        industry_pe=industry_pe,
        filing=filing,
        as_of=as_of,
        source_commit=args.source_commit,
    )

    pd.DataFrame(industry_change).to_csv(
        out / "industry_change_probe.csv", index=False
    )
    pd.DataFrame(industry_pe).to_csv(out / "industry_pe_probe.csv", index=False)
    pd.DataFrame(filing).to_csv(out / "filing_state_probe.csv", index=False)
    write_json(out / "probe_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
