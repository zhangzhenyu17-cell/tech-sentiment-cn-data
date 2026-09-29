from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSION_CLOSE = time(15, 0)
FORMER_PREOPEN_SLA = time(5, 30)
SHADOW_DECISION_CUTOFF = time(8, 45)
FIRST_ELIGIBLE_EXECUTION = time(9, 30)


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


def validate_capture_pair_v3(
    *,
    market_session_date: str,
    decision_date: str,
    observed_at: datetime,
    client: Any,
) -> dict[str, object]:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
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
    former_sla = datetime.combine(decision.date(), FORMER_PREOPEN_SLA, tzinfo=SHANGHAI)
    decision_cutoff = datetime.combine(
        decision.date(), SHADOW_DECISION_CUTOFF, tzinfo=SHANGHAI
    )
    first_execution = datetime.combine(
        decision.date(), FIRST_ELIGIBLE_EXECUTION, tzinfo=SHANGHAI
    )
    now = observed_at.astimezone(SHANGHAI)
    if now < session_close:
        raise ValueError("V3 source observation may not begin before market-session close")

    if now <= former_sla:
        timing_class = "PREOPEN_BEFORE_FORMER_0530_SLA"
    elif now <= decision_cutoff:
        timing_class = "LATE_PREOPEN_BEFORE_SHADOW_DECISION_CUTOFF"
    elif now < first_execution:
        timing_class = "POST_CUTOFF_PRE_EXECUTION_LATE_RECOVERY"
    else:
        timing_class = "POST_EXECUTION_LATE_RECOVERY"

    return {
        "timing_contract": "PROSPECTIVE_CAPTURE_TIMING_V3",
        "activation_mode": "SHADOW_ONLY_NO_FORMAL_EVIDENCE_HANDOFF",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "session_close_asia_shanghai": session_close.isoformat(),
        "former_preopen_sla_asia_shanghai": former_sla.isoformat(),
        "shadow_decision_cutoff_asia_shanghai": decision_cutoff.isoformat(),
        "first_eligible_execution_at": first_execution.isoformat(),
        "observed_at_asia_shanghai": now.isoformat(),
        "timing_class": timing_class,
        "capture_allowed": True,
        "shadow_decision_eligible_by_time": now <= decision_cutoff,
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "forward_outcomes_read": False,
    }


__all__ = [
    "SHANGHAI",
    "SESSION_CLOSE",
    "FORMER_PREOPEN_SLA",
    "SHADOW_DECISION_CUTOFF",
    "FIRST_ELIGIBLE_EXECUTION",
    "a_share_trading_dates",
    "validate_capture_pair_v3",
]
