from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Iterable
import numpy as np
import pandas as pd

from .bounded_retry import call_with_bounded_network_retry
from .official_exchange_transport import fetch_official_json

SSE_ETF_SHARE_SOURCE_ID = "SSE_ETF_SCALE_DAILY"
SSE_ETF_SHARE_SOURCE_URL = "https://www.sse.com.cn/assortment/fund/etf/list/scale/"
SSE_ETF_SCALE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_ETF_SCALE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SSE_ETF_EXACT_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_ETFJBXX_JJGM_SEARCH_L"
SSE_ETF_DETAIL_PAGE_TEMPLATE = (
    "https://www.sse.com.cn/assortment/fund/list/etfinfo/scale/"
    "index.shtml?FUNDID={fund_code}"
)
SSE_TURNOVER_QUERY_URL = SSE_ETF_SCALE_QUERY_URL
SSE_TURNOVER_SQL_ID = "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C"
SZSE_TURNOVER_QUERY_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_TURNOVER_SOURCE_ID = "SSE_DAILY_STOCK_OVERVIEW"
SSE_TURNOVER_SOURCE_URL = "https://www.sse.com.cn/market/stockdata/overview/day/"
SZSE_TURNOVER_SOURCE_ID = "SZSE_MARKET_OVERVIEW_DAILY"
SZSE_TURNOVER_SOURCE_URL = "https://www.szse.cn/market/overview/index.html"
SSE_MARGIN_SOURCE_ID = "SSE_MARGIN_SUMMARY"
SSE_MARGIN_SOURCE_URL = "https://www.sse.com.cn/market/othersdata/margin/sum/"
SZSE_MARGIN_SOURCE_ID = "SZSE_MARGIN_SUMMARY"
SZSE_MARGIN_SOURCE_URL = "https://www.szse.cn/disclosure/margin/margin/index.html"

_YUAN_UNITS = {"yuan", "CNY"}
_100M_YUAN_UNITS = {"100_million_yuan", "CNY_100M"}


@dataclass(frozen=True)
class EtfShareFetchResult:
    data: pd.DataFrame
    errors: pd.DataFrame


def _date(value: object) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _sse_etf_scale_payload_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise ValueError("SSE ETF scale response is not a JSON object")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise ValueError("SSE ETF scale response lacks result rows")
    columns = ["序号", "基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    required = {"NUM", "SEC_CODE", "SEC_NAME", "ETF_TYPE", "STAT_DATE", "TOT_VOL"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SSE ETF scale response missing fields: {sorted(missing)}")
    frame = frame.rename(
        columns={
            "NUM": "序号",
            "SEC_CODE": "基金代码",
            "SEC_NAME": "基金简称",
            "ETF_TYPE": "ETF类型",
            "STAT_DATE": "统计日期",
            "TOT_VOL": "基金份额",
        }
    )[columns].copy()
    frame["序号"] = pd.to_numeric(frame["序号"], errors="coerce")
    frame["统计日期"] = pd.to_datetime(frame["统计日期"], errors="coerce").dt.date
    frame["基金份额"] = pd.to_numeric(frame["基金份额"], errors="coerce") * 10_000.0
    return frame


def _sse_etf_exact_payload_frame(
    payload: object,
    *,
    date: str,
    fund_code: str,
) -> pd.DataFrame:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise ValueError("SSE ETF exact response lacks result rows")
    code = str(fund_code).zfill(6)
    stat_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    matches: list[dict[str, object]] = []
    for row in payload["result"]:
        if not isinstance(row, dict):
            continue
        row_code = str(row.get("SEC_CODE") or "").zfill(6)
        row_date = str(row.get("STAT_DATE") or "").strip()
        if row_code == code and row_date == stat_date:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError(
            "SSE ETF exact response requires exactly one target code/date row"
        )
    row = matches[0]
    shares_10k = pd.to_numeric(
        pd.Series([row.get("TOT_VOL")]), errors="coerce"
    ).iloc[0]
    if pd.isna(shares_10k) or float(shares_10k) <= 0:
        raise ValueError("SSE ETF exact response contains invalid TOT_VOL")
    return pd.DataFrame(
        [
            {
                "序号": pd.to_numeric(row.get("NUM"), errors="coerce"),
                "基金代码": code,
                "基金简称": str(row.get("SEC_NAME") or ""),
                "ETF类型": str(row.get("ETF_TYPE") or ""),
                "统计日期": pd.Timestamp(stat_date).date(),
                "基金份额": float(shares_10k) * 10_000.0,
            }
        ]
    )


def _fetch_sse_etf_scale_direct(
    date: str,
    *,
    timeout: float = 20.0,
    plain_get: Callable[..., object] | None = None,
    browser_get: Callable[..., object] | None = None,
    browser_session_factory: Callable[[], object] | None = None,
) -> pd.DataFrame:
    """Fetch the official SSE all-ETF daily scale table."""

    if len(date) != 8 or not date.isdigit():
        raise ValueError("SSE ETF scale date must be YYYYMMDD")
    stat_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    payload = fetch_official_json(
        url=SSE_ETF_SCALE_QUERY_URL,
        params={
            "isPagination": "true",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": SSE_ETF_SCALE_SQL_ID,
            "STAT_DATE": stat_date,
        },
        referer=SSE_ETF_SHARE_SOURCE_URL,
        allowed_query_hosts=("query.sse.com.cn",),
        warmup_url=SSE_ETF_SHARE_SOURCE_URL,
        allowed_warmup_hosts=("www.sse.com.cn",),
        timeout=timeout,
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=browser_session_factory,
    )
    return _sse_etf_scale_payload_frame(payload)


def _fetch_sse_etf_scale_exact(
    date: str,
    fund_code: str,
    *,
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Fetch one exact SSE ETF/date after the bulk endpoint is transport-blocked."""

    if len(date) != 8 or not date.isdigit():
        raise ValueError("SSE ETF scale date must be YYYYMMDD")
    code = str(fund_code).zfill(6)
    stat_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    referer = SSE_ETF_DETAIL_PAGE_TEMPLATE.format(fund_code=code)
    payload = fetch_official_json(
        url=SSE_ETF_SCALE_QUERY_URL,
        params={
            "isPagination": "false",
            "sqlId": SSE_ETF_EXACT_SQL_ID,
            "SEC_CODE": code,
            "STAT_DATE": stat_date,
        },
        referer=referer,
        allowed_query_hosts=("query.sse.com.cn",),
        warmup_url=referer,
        allowed_warmup_hosts=("www.sse.com.cn",),
        timeout=timeout,
    )
    return _sse_etf_exact_payload_frame(payload, date=date, fund_code=code)


def _sse_turnover_payload_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise ValueError("SSE daily overview response lacks result rows")
    temp = pd.DataFrame(payload["result"]).T
    temp.reset_index(inplace=True)
    if len(temp) != 11:
        raise ValueError(
            f"SSE daily overview expected 11 metric rows, got {len(temp)}"
        )
    if len(temp.columns) == 5:
        temp.columns = ["单日情况", "主板A", "主板B", "科创板", "股票"]
        temp["股票回购"] = pd.NA
    elif len(temp.columns) == 4:
        temp.columns = ["单日情况", "主板A", "主板B", "科创板"]
        temp["股票"] = pd.NA
        temp["股票回购"] = pd.NA
    elif len(temp.columns) == 6:
        temp.columns = ["单日情况", "主板A", "主板B", "科创板", "股票回购", "股票"]
    else:
        raise ValueError(
            f"SSE daily overview has unexpected product-column count {len(temp.columns)}"
        )
    temp["单日情况"] = [
        "市价总值",
        "成交量",
        "平均市盈率",
        "换手率",
        "成交金额",
        "-",
        "流通市值",
        "流通换手率",
        "报告日期",
        "挂牌数",
        "-",
    ]
    temp = temp[~temp["单日情况"].isin(["-", "报告日期"])].copy()
    for column in ("股票", "主板A", "主板B", "科创板", "股票回购"):
        temp[column] = pd.to_numeric(temp[column], errors="coerce")
    return temp[
        ["单日情况", "股票", "主板A", "主板B", "科创板", "股票回购"]
    ].reset_index(drop=True)


def _fetch_sse_turnover_official(date: str, *, timeout: float = 20.0) -> pd.DataFrame:
    if len(date) != 8 or not date.isdigit():
        raise ValueError("SSE turnover date must be YYYYMMDD")
    payload = fetch_official_json(
        url=SSE_TURNOVER_QUERY_URL,
        params={
            "sqlId": SSE_TURNOVER_SQL_ID,
            "PRODUCT_CODE": "01,02,03,11,17",
            "type": "inParams",
            "SEARCH_DATE": f"{date[:4]}-{date[4:6]}-{date[6:]}",
        },
        referer=SSE_TURNOVER_SOURCE_URL,
        allowed_query_hosts=("query.sse.com.cn",),
        warmup_url=SSE_TURNOVER_SOURCE_URL,
        allowed_warmup_hosts=("www.sse.com.cn",),
        timeout=timeout,
    )
    return _sse_turnover_payload_frame(payload)


def _szse_turnover_payload_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("SZSE market overview response must be a non-empty JSON array")
    rows = payload[0].get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("SZSE market overview response lacks data rows")
    parsed: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = row.get("zqlb", row.get("lbmc"))
        amount = row.get("cjje")
        if category is None or amount is None:
            continue
        parsed.append(
            {
                "证券类别": str(category).strip(),
                # The official SZSE JSON catalog reports cjje in 亿元.
                # Canonical turnover downstream is yuan, matching the prior
                # official xlsx normalization contract.
                "成交金额": pd.to_numeric(
                    str(amount).replace(",", ""), errors="coerce"
                )
                * 100_000_000.0,
            }
        )
    frame = pd.DataFrame(parsed)
    if frame.empty or {"股票", "主板B股"}.difference(set(frame["证券类别"])):
        # Older reports may label B shares simply as B股; the normalizer already
        # accepts that alias.
        if frame.empty or "股票" not in set(frame.get("证券类别", [])) or not (
            {"主板B股", "B股"} & set(frame.get("证券类别", []))
        ):
            raise ValueError("SZSE market overview lacks 股票/B股 rows")
    return frame


def _fetch_szse_turnover_official(date: str, *, timeout: float = 20.0) -> pd.DataFrame:
    if len(date) != 8 or not date.isdigit():
        raise ValueError("SZSE turnover date must be YYYYMMDD")
    referer = SZSE_TURNOVER_SOURCE_URL
    payload = fetch_official_json(
        url=SZSE_TURNOVER_QUERY_URL,
        params={
            "SHOWTYPE": "JSON",
            "CATALOGID": "1803_sczm",
            "TABKEY": "tab1",
            "txtQueryDate": f"{date[:4]}-{date[4:6]}-{date[6:]}",
            "PAGENO": "1",
            "PAGESIZE": "50",
            "random": "0.39339437497296137",
        },
        referer=referer,
        allowed_query_hosts=("www.szse.cn",),
        warmup_url=referer,
        allowed_warmup_hosts=("www.szse.cn",),
        timeout=timeout,
    )
    return _szse_turnover_payload_frame(payload)

def normalize_sse_etf_share_snapshot(
    frame: pd.DataFrame,
    *,
    observation_date: object,
    fund_codes: Iterable[str],
) -> pd.DataFrame:
    required = {"基金代码", "统计日期", "基金份额"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SSE ETF snapshot missing columns: {sorted(missing)}")
    wanted = {str(code).zfill(6) for code in fund_codes}
    out = frame[["基金代码", "统计日期", "基金份额"]].copy()
    out["fund_code"] = (
        out["基金代码"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    )
    out["date"] = pd.to_datetime(out["统计日期"], errors="coerce").dt.normalize()
    out["fund_shares"] = pd.to_numeric(out["基金份额"], errors="coerce")
    target = _date(observation_date)
    out = out[(out["fund_code"].isin(wanted)) & (out["date"] == target)].copy()
    if out.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "fund_code",
                "fund_shares",
                "unit",
                "source_identity",
                "source_url",
                "provider_interface",
                "evidence_available_date",
            ]
        )
    if out[["fund_code", "fund_shares"]].isna().any().any() or (
        out["fund_shares"] <= 0
    ).any():
        raise ValueError("SSE ETF snapshot contains invalid fund shares")
    if out["fund_code"].duplicated().any():
        raise ValueError("SSE ETF snapshot contains duplicate fund codes")
    out["unit"] = "share"
    out["source_identity"] = SSE_ETF_SHARE_SOURCE_ID
    out["source_url"] = SSE_ETF_SHARE_SOURCE_URL
    out["provider_interface"] = SSE_ETF_SCALE_QUERY_URL
    out["evidence_available_date"] = target
    return out[
        [
            "date",
            "fund_code",
            "fund_shares",
            "unit",
            "source_identity",
            "source_url",
            "provider_interface",
            "evidence_available_date",
        ]
    ].reset_index(drop=True)


def fetch_sse_etf_share_history(
    *,
    trading_dates: Iterable[object],
    fund_codes: Iterable[str],
    sleep_seconds: float = 0.05,
    fetcher: Callable[[str], pd.DataFrame] | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> EtfShareFetchResult:
    use_official_default = fetcher is None
    requested_codes = tuple(sorted({str(value).zfill(6) for value in fund_codes}))
    if not requested_codes:
        raise ValueError("at least one SSE ETF fund code is required")
    data_parts: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    dates = pd.DatetimeIndex(
        pd.to_datetime(list(trading_dates), errors="raise")
    ).normalize().unique()
    for value in dates:
        date_arg = pd.Timestamp(value).strftime("%Y%m%d")
        try:
            if use_official_default:
                try:
                    raw = call_with_bounded_network_retry(
                        lambda: _fetch_sse_etf_scale_direct(date_arg),
                        attempts=retry_attempts,
                        backoff_seconds=retry_backoff_seconds,
                    )
                except RuntimeError as bulk_exc:
                    exact_parts: list[pd.DataFrame] = []
                    exact_errors: list[str] = []
                    for code in requested_codes:
                        try:
                            exact_parts.append(
                                call_with_bounded_network_retry(
                                    lambda code=code: _fetch_sse_etf_scale_exact(
                                        date_arg, code
                                    ),
                                    attempts=retry_attempts,
                                    backoff_seconds=retry_backoff_seconds,
                                )
                            )
                        except Exception as exact_exc:
                            exact_errors.append(
                                f"{code}:{type(exact_exc).__name__}:{exact_exc}"
                            )
                    if exact_errors or not exact_parts:
                        raise RuntimeError(
                            "SSE ETF bulk and exact official transports failed; "
                            f"bulk={type(bulk_exc).__name__}:{bulk_exc}; "
                            f"exact={exact_errors}"
                        ) from bulk_exc
                    raw = pd.concat(exact_parts, ignore_index=True)
            else:
                assert fetcher is not None
                raw = call_with_bounded_network_retry(
                    lambda: fetcher(date_arg),
                    attempts=retry_attempts,
                    backoff_seconds=retry_backoff_seconds,
                )
            normalized = normalize_sse_etf_share_snapshot(
                raw, observation_date=value, fund_codes=requested_codes
            )
            if len(normalized):
                data_parts.append(normalized)
            else:
                errors.append(
                    {
                        "date": str(pd.Timestamp(value).date()),
                        "error": "NO_MATCHING_ETF_ROW",
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "date": str(pd.Timestamp(value).date()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    data = (
        pd.concat(data_parts, ignore_index=True)
        if data_parts
        else pd.DataFrame(
            columns=[
                "date",
                "fund_code",
                "fund_shares",
                "unit",
                "source_identity",
                "source_url",
                "provider_interface",
                "evidence_available_date",
            ]
        )
    )
    return EtfShareFetchResult(
        data=data, errors=pd.DataFrame(errors, columns=["date", "error"])
    )


def qualify_trailing_etf_coverage(
    history: pd.DataFrame,
    *,
    trading_dates: Iterable[object],
    fund_code: str,
    window: int = 60,
    min_coverage: float = 0.80,
) -> pd.DataFrame:
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")
    required = {"date", "fund_code", "fund_shares"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"ETF history missing columns: {sorted(missing)}")
    code = str(fund_code).zfill(6)
    cal = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    x = history.copy()
    x["date"] = pd.to_datetime(x["date"], errors="raise").dt.normalize()
    x["fund_code"] = (
        x["fund_code"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    )
    x["fund_shares"] = pd.to_numeric(x["fund_shares"], errors="coerce")
    x = x[x["fund_code"] == code]
    if x.duplicated("date").any():
        raise ValueError("ETF history has duplicate dates for fund")
    aligned = x.set_index("date")[["fund_shares"]].reindex(cal)
    aligned.index.name = "date"
    aligned["observed"] = aligned["fund_shares"].notna()
    aligned["coverage"] = (
        aligned["observed"].astype(float).rolling(window, min_periods=window).mean()
    )
    aligned["eligible"] = aligned["coverage"].ge(min_coverage) & aligned[
        "fund_shares"
    ].notna()
    aligned["fund_code"] = code
    return aligned.reset_index()[
        ["date", "fund_code", "fund_shares", "observed", "coverage", "eligible"]
    ]


def normalize_sse_a_share_turnover(
    frame: pd.DataFrame, *, observation_date: object
) -> dict[str, object]:
    required = {"单日情况", "主板A", "科创板"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SSE daily overview missing columns: {sorted(missing)}")
    rows = frame[frame["单日情况"].astype(str) == "成交金额"]
    if len(rows) != 1:
        raise ValueError("SSE daily overview must contain exactly one 成交金额 row")
    main_a = pd.to_numeric(rows.iloc[0]["主板A"], errors="coerce")
    star = pd.to_numeric(rows.iloc[0]["科创板"], errors="coerce")
    if not np.isfinite(main_a) or not np.isfinite(star) or main_a < 0 or star < 0:
        raise ValueError("SSE A-share turnover is invalid")
    return {
        "date": _date(observation_date),
        "sse_a_share_turnover_yuan": float(main_a + star) * 100_000_000.0,
        "source_identity": SSE_TURNOVER_SOURCE_ID,
        "source_url": SSE_TURNOVER_SOURCE_URL,
        "source_unit": "100_million_yuan",
    }


def normalize_szse_a_share_turnover(
    frame: pd.DataFrame, *, observation_date: object
) -> dict[str, object]:
    """D1: SZSE A-share turnover is stock total minus B-share turnover."""
    required = {"证券类别", "成交金额"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SZSE market overview missing columns: {sorted(missing)}")
    x = frame[["证券类别", "成交金额"]].copy()
    x["证券类别"] = x["证券类别"].astype(str).str.strip()
    x["成交金额"] = pd.to_numeric(x["成交金额"], errors="coerce")
    stock = x.loc[x["证券类别"] == "股票", "成交金额"]
    b_share = x.loc[x["证券类别"].isin(["主板B股", "B股"]), "成交金额"]
    if len(stock) != 1 or len(b_share) != 1:
        raise ValueError("SZSE overview requires one 股票 row and one B-share row")
    total = float(stock.iloc[0])
    b_value = float(b_share.iloc[0])
    a_value = total - b_value
    if not np.isfinite(a_value) or min(total, b_value, a_value) < 0:
        raise ValueError("SZSE A-share turnover is invalid")
    return {
        "date": _date(observation_date),
        "szse_a_share_turnover_yuan": a_value,
        "source_identity": SZSE_TURNOVER_SOURCE_ID,
        "source_url": SZSE_TURNOVER_SOURCE_URL,
        "source_unit": "yuan",
    }


def combine_sse_szse_a_share_turnover(
    sse: pd.DataFrame, szse: pd.DataFrame
) -> pd.DataFrame:
    required_sse = {"date", "sse_a_share_turnover_yuan"}
    required_szse = {"date", "szse_a_share_turnover_yuan"}
    if required_sse - set(sse.columns) or required_szse - set(szse.columns):
        raise ValueError("exchange turnover inputs lack required columns")
    left, right = sse.copy(), szse.copy()
    left["date"] = pd.to_datetime(left["date"], errors="raise").dt.normalize()
    right["date"] = pd.to_datetime(right["date"], errors="raise").dt.normalize()
    out = left[["date", "sse_a_share_turnover_yuan"]].merge(
        right[["date", "szse_a_share_turnover_yuan"]],
        on="date",
        how="inner",
        validate="one_to_one",
    )
    out["amount"] = pd.to_numeric(
        out["sse_a_share_turnover_yuan"], errors="coerce"
    ) + pd.to_numeric(out["szse_a_share_turnover_yuan"], errors="coerce")
    if out["amount"].isna().any() or (out["amount"] <= 0).any():
        raise ValueError("combined SSE/SZSE A-share turnover is invalid")
    out["scope"] = "SSE_SZSE_A_SHARES"
    out["canonical_all_a_state"] = "INCOMPLETE_BSE_NOT_INCLUDED"
    return out.sort_values("date").reset_index(drop=True)


def qualify_financing_yuan(
    frame: pd.DataFrame,
    *,
    min_plausible_yuan: float = 1e10,
    max_plausible_yuan: float = 2e13,
    max_cross_exchange_ratio: float = 20.0,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """D1: SSE raw is yuan; SZSE raw is 100-million-yuan; canonical is yuan."""
    required = {
        "date",
        "sse_financing_balance",
        "szse_financing_balance",
        "sse_source_unit",
        "szse_source_unit",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"financing input missing columns: {sorted(missing)}")
    x = frame.copy()
    x["date"] = pd.to_datetime(x["date"], errors="raise").dt.normalize()
    if x["date"].duplicated().any():
        raise ValueError("financing input has duplicate dates")
    units_ok = x["sse_source_unit"].isin(_YUAN_UNITS) & x[
        "szse_source_unit"
    ].isin(_100M_YUAN_UNITS)
    if not units_ok.all():
        return pd.DataFrame(), {
            "state": "QUARANTINED_UNIT_UNVERIFIED",
            "n": int(len(x)),
        }
    for col in ("sse_financing_balance", "szse_financing_balance"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    numeric = x.dropna(
        subset=["sse_financing_balance", "szse_financing_balance"]
    ).copy()
    if numeric.empty:
        return pd.DataFrame(), {"state": "DATA_INSUFFICIENT", "n": 0}
    numeric["sse_financing_balance_yuan"] = numeric["sse_financing_balance"]
    numeric["szse_financing_balance_yuan"] = (
        numeric["szse_financing_balance"] * 100_000_000.0
    )
    in_range = numeric["sse_financing_balance_yuan"].between(
        min_plausible_yuan, max_plausible_yuan
    ) & numeric["szse_financing_balance_yuan"].between(
        min_plausible_yuan, max_plausible_yuan
    )
    if not in_range.all():
        return pd.DataFrame(), {
            "state": "QUARANTINED_UNIT_MISMATCH",
            "n": int(len(numeric)),
        }
    med_sse = float(numeric["sse_financing_balance_yuan"].median())
    med_sz = float(numeric["szse_financing_balance_yuan"].median())
    ratio = max(med_sse / med_sz, med_sz / med_sse)
    if not np.isfinite(ratio) or ratio > max_cross_exchange_ratio:
        return pd.DataFrame(), {
            "state": "QUARANTINED_UNIT_MISMATCH",
            "n": int(len(numeric)),
            "median_scale_ratio": ratio,
        }
    out = numeric[
        [
            "date",
            "sse_financing_balance",
            "szse_financing_balance",
            "sse_financing_balance_yuan",
            "szse_financing_balance_yuan",
        ]
    ].copy()
    out["financing_balance_yuan"] = (
        out["sse_financing_balance_yuan"] + out["szse_financing_balance_yuan"]
    )
    out["sse_source_unit"] = "CNY"
    out["szse_source_unit"] = "CNY_100M"
    out["canonical_unit"] = "CNY"
    out["sse_source_identity"] = SSE_MARGIN_SOURCE_ID
    out["szse_source_identity"] = SZSE_MARGIN_SOURCE_ID
    out["sse_source_url"] = SSE_MARGIN_SOURCE_URL
    out["szse_source_url"] = SZSE_MARGIN_SOURCE_URL
    return out.reset_index(drop=True), {
        "state": "CANONICAL_UNIT_QUALIFIED",
        "n": int(len(out)),
        "median_scale_ratio": ratio,
        "sse_raw_unit": "CNY",
        "szse_raw_unit": "CNY_100M",
        "canonical_unit": "CNY",
    }


@dataclass(frozen=True)
class ExchangeTurnoverFetchResult:
    sse: pd.DataFrame
    szse: pd.DataFrame
    combined: pd.DataFrame
    errors: pd.DataFrame


def fetch_sse_szse_a_share_turnover_history(
    *,
    trading_dates: Iterable[object],
    sleep_seconds: float = 0.05,
    sse_fetcher: Callable[[str], pd.DataFrame] | None = None,
    szse_fetcher: Callable[[str], pd.DataFrame] | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> ExchangeTurnoverFetchResult:
    if sse_fetcher is None:
        sse_fetcher = _fetch_sse_turnover_official
    if szse_fetcher is None:
        szse_fetcher = _fetch_szse_turnover_official
    sse_rows: list[dict[str, object]] = []
    szse_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    dates = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    for value in dates:
        date_arg = pd.Timestamp(value).strftime("%Y%m%d")
        try:
            sse_rows.append(
                normalize_sse_a_share_turnover(
                    call_with_bounded_network_retry(
                        lambda: sse_fetcher(date_arg),
                        attempts=retry_attempts,
                        backoff_seconds=retry_backoff_seconds,
                    ),
                    observation_date=value,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "date": str(pd.Timestamp(value).date()),
                    "exchange": "SSE",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        try:
            szse_rows.append(
                normalize_szse_a_share_turnover(
                    call_with_bounded_network_retry(
                        lambda: szse_fetcher(date_arg),
                        attempts=retry_attempts,
                        backoff_seconds=retry_backoff_seconds,
                    ),
                    observation_date=value,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "date": str(pd.Timestamp(value).date()),
                    "exchange": "SZSE",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    sse = pd.DataFrame(sse_rows)
    szse = pd.DataFrame(szse_rows)
    combined = (
        combine_sse_szse_a_share_turnover(sse, szse)
        if len(sse) and len(szse)
        else pd.DataFrame(
            columns=[
                "date",
                "sse_a_share_turnover_yuan",
                "szse_a_share_turnover_yuan",
                "amount",
                "scope",
                "canonical_all_a_state",
            ]
        )
    )
    return ExchangeTurnoverFetchResult(
        sse=sse,
        szse=szse,
        combined=combined,
        errors=pd.DataFrame(errors, columns=["date", "exchange", "error"]),
    )
