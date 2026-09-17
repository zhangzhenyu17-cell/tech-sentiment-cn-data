from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd


SSE_TRADING_CALENDAR_SOURCE_ID = "SSE_ANNUAL_HOLIDAY_CLOSURE_NOTICES"
SSE_TRADING_CALENDAR_PROVIDER = "SHANGHAI_STOCK_EXCHANGE"
REQUIRED_COLUMNS = {
    "year",
    "holiday",
    "closed_start",
    "closed_end",
    "announcement_id",
    "source_url",
}


@dataclass(frozen=True)
class OfficialTradingCalendar:
    dates: pd.DataFrame
    source_identity: str
    provider: str
    reference_sha256: str
    announcement_ids: tuple[str, ...]
    source_urls: tuple[str, ...]


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_official_sse_url(value: object) -> bool:
    parsed = urlparse(str(value).strip())
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "sse.com.cn" or host.endswith(".sse.com.cn"))


def _required_years(start: pd.Timestamp, end: pd.Timestamp) -> set[int]:
    return set(range(int(start.year), int(end.year) + 1))


def load_sse_holiday_closures(path: str | Path) -> tuple[pd.DataFrame, str]:
    reference = Path(path)
    if not reference.is_file():
        raise FileNotFoundError(f"official SSE calendar reference missing: {reference}")
    frame = pd.read_csv(reference, dtype={"year": "int64", "announcement_id": "string", "source_url": "string"})
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"official SSE calendar reference missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("official SSE calendar reference is empty")

    x = frame.copy()
    x["closed_start"] = pd.to_datetime(x["closed_start"], errors="raise").dt.normalize()
    x["closed_end"] = pd.to_datetime(x["closed_end"], errors="raise").dt.normalize()
    if bool((x["closed_end"] < x["closed_start"]).any()):
        raise ValueError("official SSE closure has closed_end before closed_start")
    if bool(x["announcement_id"].isna().any()) or bool(x["announcement_id"].astype(str).str.strip().eq("").any()):
        raise ValueError("official SSE closure announcement identity is incomplete")
    if bool(x["source_url"].isna().any()) or not bool(x["source_url"].map(_is_official_sse_url).all()):
        raise ValueError("official SSE closure source URL must be an SSE HTTPS URL")
    if bool((x["year"] != x["closed_end"].dt.year).any()):
        # A notice may begin on Dec 31 of the prior calendar year, but it belongs
        # to the following exchange-year schedule. Accept that one bounded case.
        crossing = (x["closed_start"].dt.year == x["year"] - 1) & (x["closed_end"].dt.year == x["year"])
        if not bool(((x["year"] == x["closed_end"].dt.year) | crossing).all()):
            raise ValueError("official SSE closure row is inconsistent with its exchange year")
    return x, _file_sha256(reference)


def build_sse_trading_calendar(
    *,
    start_date: object,
    end_date: object,
    reference_path: str | Path,
) -> OfficialTradingCalendar:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if pd.isna(start) or pd.isna(end):
        raise ValueError("start_date and end_date must be valid dates")
    if end < start:
        raise ValueError("end_date must not precede start_date")

    closures, reference_sha256 = load_sse_holiday_closures(reference_path)
    covered_years = set(int(value) for value in closures["year"].unique())
    missing_years = sorted(_required_years(start, end) - covered_years)
    if missing_years:
        raise ValueError(f"official SSE closure reference lacks years: {missing_years}")

    weekdays = pd.DatetimeIndex(pd.date_range(start=start, end=end, freq="B")).normalize()
    closed_dates: set[pd.Timestamp] = set()
    for _, row in closures.iterrows():
        interval = pd.date_range(row["closed_start"], row["closed_end"], freq="D")
        closed_dates.update(pd.Timestamp(value).normalize() for value in interval)
    dates = weekdays[~weekdays.isin(sorted(closed_dates))]
    if len(dates) == 0:
        raise ValueError("official SSE calendar resolved no trading dates")

    output = pd.DataFrame(
        {
            "date": dates,
            "source_identity": SSE_TRADING_CALENDAR_SOURCE_ID,
            "provider": SSE_TRADING_CALENDAR_PROVIDER,
            "reference_sha256": reference_sha256,
            "calendar_semantics": "MONDAY_FRIDAY_MINUS_OFFICIAL_SSE_HOLIDAY_CLOSURE_INTERVALS",
        }
    )
    if output["date"].duplicated().any() or not output["date"].is_monotonic_increasing:
        raise ValueError("official SSE calendar dates must be unique and increasing")

    relevant = closures[
        (closures["closed_end"] >= start) & (closures["closed_start"] <= end)
    ].copy()
    announcement_ids = tuple(sorted(set(relevant["announcement_id"].astype(str))))
    source_urls = tuple(sorted(set(relevant["source_url"].astype(str))))
    if not announcement_ids or not source_urls:
        raise ValueError("official SSE calendar lacks provenance for requested interval")
    return OfficialTradingCalendar(
        dates=output,
        source_identity=SSE_TRADING_CALENDAR_SOURCE_ID,
        provider=SSE_TRADING_CALENDAR_PROVIDER,
        reference_sha256=reference_sha256,
        announcement_ids=announcement_ids,
        source_urls=source_urls,
    )


__all__ = [
    "OfficialTradingCalendar",
    "SSE_TRADING_CALENDAR_PROVIDER",
    "SSE_TRADING_CALENDAR_SOURCE_ID",
    "build_sse_trading_calendar",
    "load_sse_holiday_closures",
]
