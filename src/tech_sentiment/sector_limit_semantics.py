from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


# The URLs below are rule/provenance references only.  They are deliberately
# exchange/official sources rather than model-study outputs.
SSE_MAIN_RULE_SOURCE = (
    "https://www.sse.com.cn/aboutus/publication/factbook/documents/"
    "c/10170564/files/300a2dcef0f0452794683bd1bfbf65b0.pdf"
)
SSE_RISK_RULE_SOURCE = (
    "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988629.shtml"
)
SSE_2026_RISK_RULE_SOURCE = (
    "https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml"
)
SZSE_BASE_RULE_SOURCE = (
    "https://www.szse.cn/disclosure/notice/general/t20060515_499577.html"
)
CHINEXT_2020_RULE_SOURCE = (
    "https://www.szse.cn/disclosure/notice/general/t20200710_579459.html"
)
SZSE_2026_RISK_RULE_SOURCE = (
    "https://www.szse.cn/lawrules/service/member/t20260630_621403.html"
)
STAR_RULE_SOURCE = (
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/"
    "c/c_20260424_10816482.shtml"
)
BAOSTOCK_SOURCE = "https://pypi.org/project/baostock/"

SUPPORTED_BOARDS = {"SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR"}
CHINEXT_REFORM_DATE = pd.Timestamp("2020-08-24")
MAIN_RISK_10_DATE = pd.Timestamp("2026-07-06")
STAR_FIRST_TRADING_DATE = pd.Timestamp("2019-07-22")


@dataclass(frozen=True)
class StructuralLimitRule:
    limit_pct: float | None
    rule_source: str
    eligible: bool
    reason: str


def _bool01(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    mapping = {"1": True, "0": False, "true": True, "false": False, "yes": True, "no": False}
    if text not in mapping:
        raise ValueError(f"invalid {field}: {value!r}")
    return mapping[text]


def structural_limit_rule(*, date: object, board: str, is_st: bool) -> StructuralLimitRule:
    """Return the structural daily A-share percentage rule, without guessing exceptions.

    This function is outcome-free.  It captures only board/date/ST rules supported by
    exchange materials.  IPO no-limit windows, relisting, delisting-transition days,
    and other special exemptions are intentionally outside this function and must be
    supplied by a separate special-day qualification layer before ``limit_eligible``
    can become true.
    """

    day = pd.Timestamp(date).normalize()
    board = str(board).strip().upper()
    if board not in SUPPORTED_BOARDS:
        return StructuralLimitRule(None, "board_rule_unknown", False, f"unsupported board {board!r}")

    if board == "STAR":
        if day < STAR_FIRST_TRADING_DATE:
            return StructuralLimitRule(None, STAR_RULE_SOURCE, False, "STAR predates first trading date")
        return StructuralLimitRule(20.0, STAR_RULE_SOURCE, True, "STAR structural rule")

    if board == "CHINEXT":
        if day >= CHINEXT_REFORM_DATE:
            return StructuralLimitRule(20.0, CHINEXT_2020_RULE_SOURCE, True, "ChiNext post-reform rule")
        if is_st:
            return StructuralLimitRule(5.0, CHINEXT_2020_RULE_SOURCE, True, "ChiNext pre-reform risk-warning rule")
        return StructuralLimitRule(10.0, SZSE_BASE_RULE_SOURCE, True, "ChiNext pre-reform ordinary rule")

    # Main-board risk-warning limits changed from 5% to 10% on 2026-07-06.
    if is_st and day < MAIN_RISK_10_DATE:
        source = SSE_RISK_RULE_SOURCE if board == "SSE_MAIN" else SZSE_BASE_RULE_SOURCE
        return StructuralLimitRule(5.0, source, True, "main-board pre-2026 risk-warning rule")
    if is_st:
        source = SSE_2026_RISK_RULE_SOURCE if board == "SSE_MAIN" else SZSE_2026_RISK_RULE_SOURCE
        return StructuralLimitRule(10.0, source, True, "main-board 2026 risk-warning rule")

    source = SSE_MAIN_RULE_SOURCE if board == "SSE_MAIN" else SZSE_BASE_RULE_SOURCE
    return StructuralLimitRule(10.0, source, True, "main-board ordinary rule")


def enrich_baostock_structural_limit_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach auditable structural limit semantics to BaoStock-style daily rows.

    Required input fields are the BaoStock daily identity/status fields plus an
    explicit board and a separately audited ``special_day_status``.  The latter is
    intentionally required so BaoStock ``isST``/``tradestatus`` cannot silently be
    mistaken for complete IPO/relisting/special-day evidence.

    ``special_day_status`` values:
      * ``ordinary``: an external lifecycle/special-day audit found no exemption;
      * ``no_limit``: an externally evidenced no-price-limit day;
      * ``unknown``: special-day status is not yet evidenced.
    """

    required = {"date", "code", "board", "tradestatus", "isST", "special_day_status"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"BaoStock limit rows missing columns: {sorted(missing)}")

    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    if out.duplicated(subset=["date", "code"], keep=False).any():
        raise ValueError("duplicate BaoStock stock-day rows")

    limit_pct: list[float | None] = []
    eligible: list[bool] = []
    sources: list[str] = []
    reasons: list[str] = []

    for row in out.itertuples(index=False):
        trading = _bool01(getattr(row, "tradestatus"), field="tradestatus")
        is_st = _bool01(getattr(row, "isST"), field="isST")
        special = str(getattr(row, "special_day_status")).strip().lower()
        if special not in {"ordinary", "no_limit", "unknown"}:
            raise ValueError(f"invalid special_day_status: {special!r}")

        rule = structural_limit_rule(date=getattr(row, "date"), board=getattr(row, "board"), is_st=is_st)
        limit_pct.append(rule.limit_pct)

        if not trading:
            eligible.append(False)
            sources.append(f"{BAOSTOCK_SOURCE};tradestatus=0")
            reasons.append("suspended_or_not_trading")
        elif special == "no_limit":
            eligible.append(False)
            sources.append(f"{rule.rule_source};special_day=no_limit")
            reasons.append("explicit_no_limit_special_day")
        elif special == "unknown":
            eligible.append(False)
            sources.append(f"{rule.rule_source};special_day=unknown")
            reasons.append("special_day_status_not_evidenced")
        elif not rule.eligible or rule.limit_pct is None:
            eligible.append(False)
            sources.append(rule.rule_source)
            reasons.append(rule.reason)
        else:
            eligible.append(True)
            sources.append(f"{BAOSTOCK_SOURCE};{rule.rule_source}")
            reasons.append("structural_rule_and_status_evidenced")

    out["limit_pct"] = limit_pct
    out["limit_eligible"] = eligible
    out["limit_rule_source"] = sources
    out["limit_rule_reason"] = reasons
    return out
