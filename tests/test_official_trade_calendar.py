from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.official_trade_calendar import (
    SSE_TRADING_CALENDAR_PROVIDER,
    SSE_TRADING_CALENDAR_SOURCE_ID,
    build_sse_trading_calendar,
)


REFERENCE = Path("data/reference/sse_holiday_closures_2022_2026.csv")


def test_calendar_starts_on_target_and_excludes_2022_spring_festival():
    result = build_sse_trading_calendar(
        start_date="2022-01-04",
        end_date="2022-02-08",
        reference_path=REFERENCE,
    )
    dates = set(pd.to_datetime(result.dates["date"]))
    assert pd.Timestamp("2022-01-04") in dates
    assert pd.Timestamp("2022-01-31") not in dates
    assert pd.Timestamp("2022-02-04") not in dates
    assert pd.Timestamp("2022-02-07") in dates
    assert result.source_identity == SSE_TRADING_CALENDAR_SOURCE_ID
    assert result.provider == SSE_TRADING_CALENDAR_PROVIDER
    assert len(result.reference_sha256) == 64


def test_calendar_excludes_exchange_specific_2024_february_9_closure():
    result = build_sse_trading_calendar(
        start_date="2024-02-08",
        end_date="2024-02-20",
        reference_path=REFERENCE,
    )
    dates = set(pd.to_datetime(result.dates["date"]))
    assert pd.Timestamp("2024-02-08") in dates
    assert pd.Timestamp("2024-02-09") not in dates
    assert pd.Timestamp("2024-02-19") in dates


def test_calendar_keeps_scheduled_2026_september_18_when_explicitly_requested():
    result = build_sse_trading_calendar(
        start_date="2026-09-17",
        end_date="2026-09-18",
        reference_path=REFERENCE,
    )
    dates = pd.to_datetime(result.dates["date"]).tolist()
    assert dates == [pd.Timestamp("2026-09-17"), pd.Timestamp("2026-09-18")]


def test_calendar_fails_closed_when_requested_year_is_not_covered(tmp_path: Path):
    frame = pd.read_csv(REFERENCE)
    path = tmp_path / "closures.csv"
    frame[frame["year"] == 2025].to_csv(path, index=False)
    with pytest.raises(ValueError, match="lacks years"):
        build_sse_trading_calendar(
            start_date="2025-12-30",
            end_date="2026-01-05",
            reference_path=path,
        )


def test_calendar_rejects_non_official_provenance(tmp_path: Path):
    frame = pd.read_csv(REFERENCE)
    frame.loc[0, "source_url"] = "https://example.invalid/calendar"
    path = tmp_path / "closures.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="SSE HTTPS URL"):
        build_sse_trading_calendar(
            start_date="2022-01-04",
            end_date="2022-01-10",
            reference_path=path,
        )


def test_calendar_reference_hash_is_deterministic():
    first = build_sse_trading_calendar(
        start_date="2022-01-04",
        end_date="2026-09-17",
        reference_path=REFERENCE,
    )
    second = build_sse_trading_calendar(
        start_date="2022-01-04",
        end_date="2026-09-17",
        reference_path=REFERENCE,
    )
    assert first.reference_sha256 == second.reference_sha256
    assert first.dates.astype(str).equals(second.dates.astype(str))
    assert first.announcement_ids == second.announcement_ids
    assert first.source_urls == second.source_urls
