from pathlib import Path

import pandas as pd

from tech_sentiment.pit_price_materialization import materialize_pit_stock_prices


def test_price_materialization_resumes_exact_symbol_year_chunks(tmp_path: Path):
    calls: list[tuple[str, str, str, str]] = []

    def fetcher(symbol: str, *, start_date: str, end_date: str, adjust: str, provider: str):
        calls.append((symbol, start_date, end_date, provider))
        dates = pd.bdate_range(start_date, end_date)
        return pd.DataFrame(
            {
                "date": dates,
                "symbol": symbol,
                "close": range(1, len(dates) + 1),
                "provider": provider,
            }
        )

    kwargs = dict(
        symbols=["600000"],
        start_date="2025-12-29",
        end_date="2026-01-05",
        source_commit="abc",
        checkpoint_dir=tmp_path,
        fetcher=fetcher,
    )
    fresh = materialize_pit_stock_prices(**kwargs)
    assert fresh.summary["executed_chunks"] == 2
    first_call_count = len(calls)
    resumed = materialize_pit_stock_prices(**kwargs)
    assert len(calls) == first_call_count
    assert resumed.summary["resumed_chunks"] == 2
    assert resumed.prices[["symbol", "date", "close"]].astype(str).equals(
        fresh.prices[["symbol", "date", "close"]].astype(str)
    )
