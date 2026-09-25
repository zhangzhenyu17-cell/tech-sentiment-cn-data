from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSION_CLOSE = time(15, 0)
DATA_FREEZE_DEADLINE = time(5, 30)
FIRST_ELIGIBLE_EXECUTION = time(9, 30)
MIN_BUFFER_HOURS = 4


def a_share_trading_dates(client: Any) -> pd.DatetimeIndex:
    raw = client.tool_trade_date_hist_sina()
    if raw is None or len(raw) == 0:
        raise RuntimeError("A-share trading calendar source returned no rows")
    column = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    return (
        pd.DatetimeIndex(pd.to_datetime(raw[column], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )


def first_a_share_trading_day_after(*, cutoff_date: str, client: Any) -> str:
    cutoff = pd.Timestamp(cutoff_date).normalize()
    dates = a_share_trading_dates(client)
    later = dates[dates > cutoff]
    if len(later) == 0:
        raise ValueError("trading calendar has no A-share session after cutoff_date")
    return later[0].strftime("%Y-%m-%d")


def validate_preopen_capture_window(
    *,
    market_session_date: str,
    decision_date: str,
    captured_at: datetime,
    client: Any,
) -> dict[str, str | int]:
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    session = pd.Timestamp(market_session_date).normalize()
    decision = pd.Timestamp(decision_date).normalize()
    dates = a_share_trading_dates(client)
    if session not in set(dates):
        raise ValueError("market_session_date is not a confirmed A-share trading day")
    if decision not in set(dates):
        raise ValueError("decision_date is not a confirmed A-share trading day")
    later = dates[dates > session]
    if len(later) == 0 or later[0] != decision:
        raise ValueError("decision_date must be the immediate next A-share trading day")

    session_close = datetime.combine(session.date(), SESSION_CLOSE, tzinfo=SHANGHAI)
    freeze_deadline = datetime.combine(
        decision.date(), DATA_FREEZE_DEADLINE, tzinfo=SHANGHAI
    )
    first_execution = datetime.combine(
        decision.date(), FIRST_ELIGIBLE_EXECUTION, tzinfo=SHANGHAI
    )
    if int((first_execution - freeze_deadline).total_seconds()) < MIN_BUFFER_HOURS * 3600:
        raise ValueError("pre-open data-freeze buffer is less than four hours")

    now = captured_at.astimezone(SHANGHAI)
    if now < session_close:
        raise ValueError("pre-open capture may not begin before market-session close")
    if now > freeze_deadline:
        raise ValueError(
            "PREOPEN_DATA_FREEZE_DEADLINE_PASSED: all prospective inputs must be "
            "collected no later than 05:30 Asia/Shanghai"
        )
    return {
        "timing_contract": "PROSPECTIVE_TIMING_V2",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "session_close_asia_shanghai": session_close.isoformat(),
        "data_freeze_deadline_asia_shanghai": freeze_deadline.isoformat(),
        "first_eligible_execution_at": first_execution.isoformat(),
        "minimum_preopen_buffer_hours": MIN_BUFFER_HOURS,
    }


__all__ = [
    "DATA_FREEZE_DEADLINE",
    "FIRST_ELIGIBLE_EXECUTION",
    "MIN_BUFFER_HOURS",
    "a_share_trading_dates",
    "first_a_share_trading_day_after",
    "validate_preopen_capture_window",
]
