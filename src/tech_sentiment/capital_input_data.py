from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Callable, Iterable
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from .bounded_retry import call_with_bounded_network_retry

SSE_ETF_SHARE_SOURCE_ID = "SSE_ETF_SCALE_DAILY"
SSE_ETF_SHARE_SOURCE_URL = "https://www.sse.com.cn/assortment/fund/etf/list/scale/"
SSE_ETF_SCALE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_ETF_SCALE_SOA_QUERY_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_ETF_SCALE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SSE_TURNOVER_SOURCE_ID = "SSE_DAILY_STOCK_OVERVIEW"
SSE_TURNOVER_SOURCE_URL = "https://www.sse.com.cn/market/stockdata/overview/day/"
SSE_TURNOVER_HISTORICAL_SOURCE_URL = "https://www.sse.com.cn/market/stockdata/overview/day/index_his.shtml"
SSE_TURNOVER_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_TURNOVER_HISTORICAL_SQL_ID = "COMMON_SSE_SJ_GPSJ_CJGK_DAYCJGK_C"
SSE_TURNOVER_HISTORICAL_CUTOFF = pd.Timestamp("2021-12-24")
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


def _validate_sse_response_url(response: object, *, expected_host: str, default_url: str) -> None:
    final_url = str(getattr(response, "url", default_url) or default_url)
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != expected_host:
        raise ValueError("SSE ETF scale transport redirected outside official HTTPS host")


def _validate_sse_query_response_url(response: object) -> None:
    _validate_sse_response_url(
        response,
        expected_host="query.sse.com.cn",
        default_url=SSE_ETF_SCALE_QUERY_URL,
    )


def _sse_payload_from_response(response: object) -> dict[str, object]:
    """Decode strict SSE JSON, accepting JSONP only when it wraps one JSON object."""

    try:
        payload = response.json()
    except Exception:
        text = str(getattr(response, "text", "") or "").strip()
        match = re.fullmatch(r"[A-Za-z_$][\w$]*\((.*)\)\s*;?", text, flags=re.S)
        if match is None:
            raise
        payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("SSE ETF scale response is not a JSON object")
    return payload


def _sse_frame_from_response(response: object, *, interface_url: str) -> pd.DataFrame:
    response.raise_for_status()
    _validate_sse_response_url(
        response,
        expected_host="query.sse.com.cn",
        default_url=interface_url,
    )
    frame = _sse_etf_scale_payload_frame(_sse_payload_from_response(response))
    frame.attrs["provider_interface"] = interface_url
    return frame


def _fetch_sse_etf_scale_direct(
    date: str,
    *,
    timeout: float = 20.0,
    plain_get: Callable[..., object] | None = None,
    browser_get: Callable[..., object] | None = None,
    browser_session_factory: Callable[[], object] | None = None,
) -> pd.DataFrame:
    """Fetch SSE ETF shares through bounded official-interface fallbacks.

    All variants use the same official query host, the same frozen SQL identity,
    the same requested STAT_DATE, and the same raw TOT_VOL semantics. The final
    fallback uses SSE's sibling commonSoaQuery endpoint rather than weakening the
    evidence source or accepting a stale/alternate date.
    """

    if len(date) != 8 or not date.isdigit():
        raise ValueError("SSE ETF scale date must be YYYYMMDD")
    stat_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    paged_params = {
        "isPagination": "true",
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": SSE_ETF_SCALE_SQL_ID,
        "STAT_DATE": stat_date,
    }
    soa_params = {
        "isPagination": "false",
        "sqlId": SSE_ETF_SCALE_SQL_ID,
        "STAT_DATE": stat_date,
    }
    headers = {
        "Referer": SSE_ETF_SHARE_SOURCE_URL,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ),
    }
    errors: list[str] = []

    if plain_get is None:
        import requests as plain_requests
        plain_get = plain_requests.get

    try:
        response = plain_get(
            SSE_ETF_SCALE_QUERY_URL,
            params=paged_params,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        return _sse_frame_from_response(
            response, interface_url=SSE_ETF_SCALE_QUERY_URL
        )
    except Exception as exc:
        errors.append(f"plain-common:{type(exc).__name__}:{exc}")

    curl_requests = None
    if browser_get is None or browser_session_factory is None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover - installed by data extra
            raise RuntimeError(
                "curl_cffi is required for SSE ETF browser transport fallback"
            ) from exc
    if browser_get is None:
        browser_get = curl_requests.get

    try:
        response = browser_get(
            SSE_ETF_SCALE_QUERY_URL,
            params=paged_params,
            headers=headers,
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=True,
        )
        return _sse_frame_from_response(
            response, interface_url=SSE_ETF_SCALE_QUERY_URL
        )
    except Exception as exc:
        errors.append(f"browser-common:{type(exc).__name__}:{exc}")

    if browser_session_factory is None:
        browser_session_factory = curl_requests.Session

    session = browser_session_factory()
    try:
        bootstrap_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": headers["Accept-Language"],
            "User-Agent": headers["User-Agent"],
        }
        try:
            bootstrap = session.get(
                SSE_ETF_SHARE_SOURCE_URL,
                headers=bootstrap_headers,
                impersonate="chrome",
                timeout=min(timeout, 10.0),
                allow_redirects=True,
            )
            _validate_sse_response_url(
                bootstrap,
                expected_host="www.sse.com.cn",
                default_url=SSE_ETF_SHARE_SOURCE_URL,
            )
        except Exception as exc:
            errors.append(f"session-bootstrap:{type(exc).__name__}:{exc}")

        try:
            response = session.get(
                SSE_ETF_SCALE_QUERY_URL,
                params=paged_params,
                headers=headers,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            return _sse_frame_from_response(
                response, interface_url=SSE_ETF_SCALE_QUERY_URL
            )
        except Exception as exc:
            errors.append(f"session-common:{type(exc).__name__}:{exc}")

        try:
            response = session.get(
                SSE_ETF_SCALE_SOA_QUERY_URL,
                params=soa_params,
                headers=headers,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            return _sse_frame_from_response(
                response, interface_url=SSE_ETF_SCALE_SOA_QUERY_URL
            )
        except Exception as exc:
            errors.append(f"session-soa:{type(exc).__name__}:{exc}")

        # The SSE page historically uses JSONP for this same SQL contract.
        # Keep it as a final protocol variant on the canonical commonQuery URL.
        jsonp_params = dict(paged_params)
        jsonp_params["jsonCallBack"] = "jsonpCallbackETFScale"
        try:
            response = session.get(
                SSE_ETF_SCALE_QUERY_URL,
                params=jsonp_params,
                headers={**headers, "Accept": "*/*"},
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            return _sse_frame_from_response(
                response, interface_url=SSE_ETF_SCALE_QUERY_URL
            )
        except Exception as exc:
            errors.append(f"session-jsonp:{type(exc).__name__}:{exc}")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    raise RuntimeError(
        "SSE ETF official HTTPS transports exhausted; " + " | ".join(errors)
    )

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
    provider_interface = str(
        frame.attrs.get("provider_interface", SSE_ETF_SCALE_QUERY_URL)
    )
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
    out["provider_interface"] = provider_interface
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
    if fetcher is None:
        fetcher = _fetch_sse_etf_scale_direct
    data_parts: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    dates = pd.DatetimeIndex(
        pd.to_datetime(list(trading_dates), errors="raise")
    ).normalize().unique()
    for value in dates:
        date_arg = pd.Timestamp(value).strftime("%Y%m%d")
        try:
            raw = call_with_bounded_network_retry(
                lambda: fetcher(date_arg),
                attempts=retry_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            normalized = normalize_sse_etf_share_snapshot(
                raw, observation_date=value, fund_codes=fund_codes
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
    aligned["observed_count"] = (
        aligned["observed"].astype(int).rolling(window, min_periods=window).sum()
    )
    aligned["coverage"] = aligned["observed_count"] / float(window)
    aligned["eligible"] = aligned["coverage"].ge(min_coverage) & aligned[
        "fund_shares"
    ].notna()
    aligned["fund_code"] = code
    return aligned.reset_index()[
        [
            "date",
            "fund_code",
            "fund_shares",
            "observed",
            "observed_count",
            "coverage",
            "eligible",
        ]
    ]


def _sse_turnover_payload_from_response(response: object) -> dict[str, object]:
    response.raise_for_status()
    _validate_sse_response_url(
        response,
        expected_host="query.sse.com.cn",
        default_url=SSE_TURNOVER_QUERY_URL,
    )
    try:
        payload = response.json()
    except Exception:
        text = str(getattr(response, "text", "") or "").strip()
        match = re.fullmatch(r"[A-Za-z_$][\\w$]*\\((.*)\\)\\s*;?", text, flags=re.S)
        if match is None:
            raise
        payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("SSE turnover response is not a JSON object")
    return payload


def _sse_historical_turnover_payload_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise ValueError("SSE historical turnover response is not a JSON object")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise ValueError("SSE historical turnover response lacks result rows")
    frame = pd.DataFrame(rows)
    required = {"PRODUCT_TYPE", "TX_AMOUNT"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"SSE historical turnover response missing fields: {sorted(missing)}"
        )
    frame["PRODUCT_TYPE"] = (
        frame["PRODUCT_TYPE"].astype(str).str.replace(r"\.0$", "", regex=True)
    )
    frame["TX_AMOUNT"] = pd.to_numeric(
        frame["TX_AMOUNT"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    main_a = frame.loc[frame["PRODUCT_TYPE"].eq("1"), "TX_AMOUNT"]
    star = frame.loc[frame["PRODUCT_TYPE"].eq("43"), "TX_AMOUNT"]
    if len(main_a) != 1 or len(star) != 1:
        raise ValueError(
            "SSE historical turnover requires exactly one main-A and one STAR row"
        )
    if main_a.isna().any() or star.isna().any():
        raise ValueError("SSE historical turnover contains invalid trade amount")
    return pd.DataFrame(
        [
            {
                "单日情况": "成交金额",
                "主板A": float(main_a.iloc[0]),
                "科创板": float(star.iloc[0]),
            }
        ]
    )


def _fetch_sse_historical_daily_overview(
    date: str,
    *,
    timeout: float = 20.0,
    plain_get: Callable[..., object] | None = None,
) -> pd.DataFrame:
    if len(date) != 8 or not date.isdigit():
        raise ValueError("SSE historical turnover date must be YYYYMMDD")
    if plain_get is None:
        import requests as plain_requests
        plain_get = plain_requests.get
    params = {
        "searchDate": f"{date[:4]}-{date[4:6]}-{date[6:]}",
        "sqlId": SSE_TURNOVER_HISTORICAL_SQL_ID,
        "stockType": "90",
    }
    headers = {
        "Referer": SSE_TURNOVER_HISTORICAL_SOURCE_URL,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ),
    }
    response = plain_get(
        SSE_TURNOVER_QUERY_URL,
        params=params,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    frame = _sse_historical_turnover_payload_frame(
        _sse_turnover_payload_from_response(response)
    )
    frame.attrs["provider_interface"] = SSE_TURNOVER_QUERY_URL
    frame.attrs["historical_sql_id"] = SSE_TURNOVER_HISTORICAL_SQL_ID
    return frame


def _fetch_sse_daily_overview_official(date: str) -> pd.DataFrame:
    observation = pd.Timestamp(
        f"{date[:4]}-{date[4:6]}-{date[6:]}"
    ).normalize()
    if observation <= SSE_TURNOVER_HISTORICAL_CUTOFF:
        return _fetch_sse_historical_daily_overview(date)
    import akshare as ak  # type: ignore
    return ak.stock_sse_deal_daily(date=date)


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
        sse_fetcher = _fetch_sse_daily_overview_official
    if szse_fetcher is None:
        import akshare as ak  # type: ignore
        szse_fetcher = lambda date: ak.stock_szse_summary(date=date)
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
