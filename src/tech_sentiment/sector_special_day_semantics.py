from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


BAOSTOCK_BASIC_SOURCE = "https://pypi.org/project/baostock/"
CHINEXT_REFORM_DATE = pd.Timestamp("2020-08-24")
STAR_FIRST_TRADING_DATE = pd.Timestamp("2019-07-22")


@dataclass(frozen=True)
class SpecialDayAudit:
    rows: int
    ordinary_rows: int
    no_limit_rows: int
    unknown_rows: int


def _bool01(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    mapping = {"1": True, "0": False, "true": True, "false": False, "yes": True, "no": False}
    if text not in mapping:
        raise ValueError(f"invalid {field}: {value!r}")
    return mapping[text]


def infer_board_from_baostock_code(code: object) -> str:
    """Map ordinary A-share BaoStock codes to the board taxonomy used by sector V0.

    This function deliberately does not classify B shares, Beijing Stock Exchange,
    funds, bonds, or other instruments. Unsupported codes fail closed.
    """

    text = str(code).strip().lower()
    if text.startswith("sh.688"):
        return "STAR"
    if text.startswith("sz.300") or text.startswith("sz.301"):
        return "CHINEXT"
    if text.startswith(("sh.600", "sh.601", "sh.603", "sh.605")):
        return "SSE_MAIN"
    if text.startswith(("sz.000", "sz.001", "sz.002", "sz.003")):
        return "SZSE_MAIN"
    return "UNKNOWN"


def _normalise_basic(stock_basic: pd.DataFrame) -> pd.DataFrame:
    required = {"code", "ipoDate", "outDate"}
    missing = required - set(stock_basic.columns)
    if missing:
        raise ValueError(f"stock_basic missing columns: {sorted(missing)}")
    basic = stock_basic.loc[:, ["code", "ipoDate", "outDate"]].copy()
    basic["code"] = basic["code"].astype(str).str.strip().str.lower()
    if basic["code"].duplicated().any():
        raise ValueError("duplicate stock_basic code rows")
    basic["ipoDate"] = pd.to_datetime(basic["ipoDate"], errors="coerce").dt.normalize()
    basic["outDate"] = pd.to_datetime(basic["outDate"].replace("", pd.NA), errors="coerce").dt.normalize()
    if basic["ipoDate"].isna().any():
        raise ValueError("stock_basic requires a valid ipoDate for every code")
    return basic


def _normalise_overrides(overrides: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["date", "code", "special_day_status", "special_day_source"]
    if overrides is None:
        return pd.DataFrame(columns=columns)
    required = set(columns)
    missing = required - set(overrides.columns)
    if missing:
        raise ValueError(f"special-day overrides missing columns: {sorted(missing)}")
    out = overrides.loc[:, columns].copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["code"] = out["code"].astype(str).str.strip().str.lower()
    out["special_day_status"] = out["special_day_status"].astype(str).str.strip().str.lower()
    allowed = {"ordinary", "no_limit", "unknown"}
    invalid = sorted(set(out["special_day_status"]) - allowed)
    if invalid:
        raise ValueError(f"invalid special_day_status overrides: {invalid}")
    if out["special_day_source"].isna().any() or out["special_day_source"].astype(str).str.strip().eq("").any():
        raise ValueError("special-day overrides require non-empty special_day_source")
    if out.duplicated(subset=["date", "code"]).any():
        raise ValueError("duplicate special-day override rows")
    return out


def derive_special_day_status(
    daily_rows: pd.DataFrame,
    stock_basic: pd.DataFrame,
    *,
    overrides: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, SpecialDayAudit]:
    """Derive outcome-free IPO/lifecycle special-day status for A-share stock days.

    Known no-limit windows encoded here are limited to rules with stable board/date
    semantics in the design period: STAR IPO first five trading sessions and
    ChiNext IPO first five trading sessions for IPOs on/after 2020-08-24.

    Main-board IPO first trading day and pre-reform ChiNext IPO first trading day
    are marked ``unknown`` rather than assigned the ordinary structural limit,
    because their listing-day mechanisms differ from ordinary 10%/5% rules.

    Relisting, delisting-transition, and any other exceptional dates must be supplied
    through ``overrides``. Overrides always take precedence and retain their source.
    No price movement is inspected when deriving these states.
    """

    required = {"date", "code", "tradestatus"}
    missing = required - set(daily_rows.columns)
    if missing:
        raise ValueError(f"daily_rows missing columns: {sorted(missing)}")

    frame = daily_rows.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["code"] = frame["code"].astype(str).str.strip().str.lower()
    if frame.duplicated(subset=["date", "code"]).any():
        raise ValueError("duplicate daily stock-day rows")

    if "board" not in frame.columns:
        frame["board"] = frame["code"].map(infer_board_from_baostock_code)
    else:
        frame["board"] = frame["board"].astype(str).str.strip().str.upper()

    basic = _normalise_basic(stock_basic)
    frame = frame.merge(basic, on="code", how="left", validate="many_to_one")
    if frame["ipoDate"].isna().any():
        missing_codes = sorted(frame.loc[frame["ipoDate"].isna(), "code"].unique())
        raise ValueError(f"missing stock_basic lifecycle rows for: {missing_codes}")

    frame["special_day_status"] = "ordinary"
    frame["special_day_source"] = BAOSTOCK_BASIC_SOURCE + ";lifecycle_baseline"
    frame["special_day_reason"] = "outside_known_ipo_special_window"

    unsupported = frame["board"].eq("UNKNOWN")
    frame.loc[unsupported, "special_day_status"] = "unknown"
    frame.loc[unsupported, "special_day_source"] = "board_rule_unknown"
    frame.loc[unsupported, "special_day_reason"] = "unsupported_board"

    before_ipo = frame["date"] < frame["ipoDate"]
    frame.loc[before_ipo, "special_day_status"] = "unknown"
    frame.loc[before_ipo, "special_day_reason"] = "date_before_ipo"

    after_out = frame["outDate"].notna() & (frame["date"] >= frame["outDate"])
    frame.loc[after_out, "special_day_status"] = "unknown"
    frame.loc[after_out, "special_day_reason"] = "at_or_after_reported_out_date"

    trading_mask = frame["tradestatus"].map(lambda value: _bool01(value, field="tradestatus"))
    frame["_trading"] = trading_mask
    frame["_post_ipo_trading_rank"] = pd.NA

    for code, idx in frame.groupby("code").groups.items():
        ordered = frame.loc[idx].sort_values("date")
        valid = ordered["_trading"] & (ordered["date"] >= ordered["ipoDate"])
        ranks = pd.Series(range(1, int(valid.sum()) + 1), index=ordered.index[valid], dtype="Int64")
        frame.loc[ranks.index, "_post_ipo_trading_rank"] = ranks

    ranks = pd.to_numeric(frame["_post_ipo_trading_rank"], errors="coerce")
    star_no_limit = (
        frame["board"].eq("STAR")
        & (frame["ipoDate"] >= STAR_FIRST_TRADING_DATE)
        & ranks.between(1, 5, inclusive="both")
    )
    chinext_no_limit = (
        frame["board"].eq("CHINEXT")
        & (frame["ipoDate"] >= CHINEXT_REFORM_DATE)
        & ranks.between(1, 5, inclusive="both")
    )
    known_no_limit = star_no_limit | chinext_no_limit
    frame.loc[known_no_limit, "special_day_status"] = "no_limit"
    frame.loc[known_no_limit, "special_day_source"] = BAOSTOCK_BASIC_SOURCE + ";exchange_ipo_window"
    frame.loc[known_no_limit, "special_day_reason"] = "first_five_trading_sessions_no_limit"

    first_trading = ranks.eq(1)
    main_or_old_chinext = frame["board"].isin({"SSE_MAIN", "SZSE_MAIN"}) | (
        frame["board"].eq("CHINEXT") & (frame["ipoDate"] < CHINEXT_REFORM_DATE)
    )
    listing_day_unknown = first_trading & main_or_old_chinext
    frame.loc[listing_day_unknown, "special_day_status"] = "unknown"
    frame.loc[listing_day_unknown, "special_day_source"] = BAOSTOCK_BASIC_SOURCE + ";listing_day_special_rule"
    frame.loc[listing_day_unknown, "special_day_reason"] = "listing_day_not_ordinary_structural_limit"

    override = _normalise_overrides(overrides)
    if not override.empty:
        keyed = override.set_index(["date", "code"])
        for row_idx, row in frame.iterrows():
            key = (row["date"], row["code"])
            if key not in keyed.index:
                continue
            item = keyed.loc[key]
            frame.at[row_idx, "special_day_status"] = str(item["special_day_status"])
            frame.at[row_idx, "special_day_source"] = str(item["special_day_source"])
            frame.at[row_idx, "special_day_reason"] = "explicit_override"

    frame = frame.drop(columns=["_trading", "_post_ipo_trading_rank"])
    counts = frame["special_day_status"].value_counts()
    audit = SpecialDayAudit(
        rows=len(frame),
        ordinary_rows=int(counts.get("ordinary", 0)),
        no_limit_rows=int(counts.get("no_limit", 0)),
        unknown_rows=int(counts.get("unknown", 0)),
    )
    return frame, audit
