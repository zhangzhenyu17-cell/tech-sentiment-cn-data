import pandas as pd

from tech_sentiment.financing_materialization import materialize_financing_history


def _calendar():
    return pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])


def _sse(_start, _end):
    return pd.DataFrame(
        {
            "信用交易日期": ["2026-01-05", "2026-01-06", "2026-01-07"],
            "融资余额": [9.0e11, 9.1e11, 9.2e11],
        }
    )


def _szse(_start, _end):
    return pd.DataFrame(
        {
            "交易日期": ["2026-01-05", "2026-01-06", "2026-01-07"],
            "融资余额": [8000.0, 8050.0, 8100.0],
        }
    )


def test_complete_bilateral_history_qualifies_under_frozen_units():
    result = materialize_financing_history(
        trading_dates=_calendar(),
        start_date="2026-01-05",
        end_date="2026-01-07",
        sse_fetcher=_sse,
        szse_range_fetcher=_szse,
    )
    assert result.summary["state"] == "QUALIFIED_INPUT"
    assert result.summary["role"] == "RESEARCH_INPUT"
    assert result.summary["bilateral_coverage"] == 1.0
    assert result.summary["sse_raw_unit"] == "CNY"
    assert result.summary["szse_raw_unit"] == "CNY_100M"
    assert result.summary["canonical_unit"] == "CNY"
    assert result.summary["multiplier_inferred_from_values"] is False
    assert result.summary["included_in_capital_regime_composite"] is False
    assert result.canonical.loc[0, "szse_financing_balance_yuan"] == 8000.0 * 1e8


def test_missing_one_exchange_day_stays_partial_and_emits_no_canonical_rail():
    def szse_partial(_start, _end):
        return pd.DataFrame(
            {
                "交易日期": ["2026-01-05", "2026-01-07"],
                "融资余额": [8000.0, 8100.0],
            }
        )

    result = materialize_financing_history(
        trading_dates=_calendar(),
        start_date="2026-01-05",
        end_date="2026-01-07",
        sse_fetcher=_sse,
        szse_range_fetcher=szse_partial,
    )
    assert result.summary["state"] == "PARTIAL_COVERAGE"
    assert result.summary["missing_days"] == 1
    assert result.canonical.empty
    missing = result.raw_aligned.loc[
        result.raw_aligned["date"] == pd.Timestamp("2026-01-06")
    ].iloc[0]
    assert pd.isna(missing["szse_financing_balance"])


def test_schema_failure_is_fail_closed_not_unit_guessed():
    def broken_sse(_start, _end):
        return pd.DataFrame({"日期": ["2026-01-05"], "未知余额": [9.0e11]})

    result = materialize_financing_history(
        trading_dates=_calendar(),
        start_date="2026-01-05",
        end_date="2026-01-07",
        sse_fetcher=broken_sse,
        szse_range_fetcher=_szse,
    )
    assert result.summary["state"] == "PARTIAL_COVERAGE"
    assert result.canonical.empty
    assert (result.errors["exchange"] == "SSE").any()
