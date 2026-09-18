from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .bounded_retry import call_with_bounded_network_retry
from .pit_public_materialization import (
    REQUIRED_PIT_COLUMNS,
    PitMaterializationResult,
    _market_available_date,
    _normalize_entity_id,
    _real_trading_calendar,
    _stable_hash,
    classify_cninfo_title,
    validate_materialized_pit_records,
)


SSE_SOURCE_ID = "SSE_ANNOUNCEMENT_ARCHIVE"
SSE_PROVIDER = "SHANGHAI_STOCK_EXCHANGE"
SSE_QUERY_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
SSE_REFERER = "https://www.sse.com.cn/disclosure/listedinfo/announcement/"

SZSE_SOURCE_ID = "SZSE_ANNOUNCEMENT_ARCHIVE"
SZSE_PROVIDER = "SHENZHEN_STOCK_EXCHANGE"
SZSE_QUERY_URL = "https://www.szse.cn/api/disc/announcement/annList"
SZSE_REFERER = "https://www.szse.cn/disclosure/listed/notice/index.html"

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class OfficialArchiveSpec:
    source_identity: str
    provider: str
    query_url: str
    entity_suffix: str


SSE_SPEC = OfficialArchiveSpec(SSE_SOURCE_ID, SSE_PROVIDER, SSE_QUERY_URL, ".SH")
SZSE_SPEC = OfficialArchiveSpec(SZSE_SOURCE_ID, SZSE_PROVIDER, SZSE_QUERY_URL, ".SZ")


def _decode_json_or_jsonp(raw: bytes) -> dict[str, object]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("official archive returned an empty response")
    if text.startswith("{"):
        payload = json.loads(text)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("official archive response is neither JSON nor JSONP")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("official archive payload must be a JSON object")
    return payload


def _get_json(url: str, *, headers: dict[str, str], timeout: float) -> dict[str, object]:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed official HTTPS endpoints
        return _decode_json_or_jsonp(response.read())


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed official HTTPS endpoints
        return _decode_json_or_jsonp(response.read())


def _year_windows(start_date: object, end_date: object) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while cursor <= end:
        window_end = min(pd.Timestamp(year=cursor.year, month=12, day=31), end)
        windows.append((cursor, window_end))
        cursor = window_end + pd.Timedelta(days=1)
    return windows


def fetch_sse_announcements(
    *,
    symbol: str,
    start_date: object,
    end_date: object,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch an issuer's official SSE announcement archive in bounded yearly windows."""

    entity_id = _normalize_entity_id(symbol)
    if not entity_id.endswith(".SH"):
        raise ValueError(f"SSE archive only applies to Shanghai symbols: {symbol}")
    rows: list[dict[str, object]] = []
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": SSE_REFERER,
        "User-Agent": _USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    for window_start, window_end in _year_windows(start_date, end_date):
        params = {
            "isPagination": "false",
            "productId": str(symbol).zfill(6),
            "keyWord": "",
            "securityType": "0101,120100,020100,020200,120200",
            "reportType2": "",
            "reportType": "ALL",
            "beginDate": window_start.strftime("%Y-%m-%d"),
            "endDate": window_end.strftime("%Y-%m-%d"),
        }
        payload = _get_json(
            f"{SSE_QUERY_URL}?{urlencode(params)}", headers=headers, timeout=timeout
        )
        data = payload.get("result")
        if not isinstance(data, list):
            page_help = payload.get("pageHelp")
            data = page_help.get("data") if isinstance(page_help, dict) else None
        if data is None:
            raise ValueError("SSE announcement payload lacks result/pageHelp.data")
        if not isinstance(data, list):
            raise ValueError("SSE announcement result is not a list")
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get("SECURITY_CODE") or item.get("securityCode") or symbol)
            code = "".join(ch for ch in code if ch.isdigit()).zfill(6)
            if code != str(symbol).zfill(6):
                continue
            title = str(item.get("TITLE") or item.get("title") or "").strip()
            published = str(item.get("SSEDATE") or item.get("publishDate") or "").strip()
            relative_url = str(item.get("URL") or item.get("url") or "").strip()
            if not title or not published or not relative_url:
                raise ValueError("SSE announcement row lacks title/date/url identity")
            source_url = (
                relative_url
                if relative_url.startswith(("http://", "https://"))
                else f"https://www.sse.com.cn{relative_url if relative_url.startswith('/') else '/' + relative_url}"
            )
            native_id = str(
                item.get("BULLETIN_ID")
                or item.get("bulletinId")
                or item.get("SSEBULLETINID")
                or ""
            ).strip()
            if not native_id:
                native_id = hashlib.sha256(
                    f"{symbol}|{published}|{title}|{source_url}".encode("utf-8")
                ).hexdigest()[:24]
            rows.append(
                {
                    "symbol": str(symbol).zfill(6),
                    "title": title,
                    "publication_time": published,
                    "document_id": native_id,
                    "source_url": source_url,
                }
            )
    if not rows:
        return pd.DataFrame(columns=["symbol", "title", "publication_time", "document_id", "source_url"])
    out = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "publication_time", "document_id", "source_url"], keep="first"
    )
    return out.sort_values(["publication_time", "document_id"]).reset_index(drop=True)


def fetch_szse_announcements(
    *,
    symbol: str,
    start_date: object,
    end_date: object,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch an issuer's official SZSE announcement archive with explicit pagination."""

    entity_id = _normalize_entity_id(symbol)
    if not entity_id.endswith(".SZ"):
        raise ValueError(f"SZSE archive only applies to Shenzhen symbols: {symbol}")
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "Origin": "https://www.szse.cn",
        "Referer": SZSE_REFERER,
        "User-Agent": _USER_AGENT,
        "X-Request-Type": "ajax",
        "X-Requested-With": "XMLHttpRequest",
    }
    rows: list[dict[str, object]] = []
    for window_start, window_end in _year_windows(start_date, end_date):
        page = 1
        while True:
            body: dict[str, object] = {
                "seDate": [window_start.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")],
                "channelCode": ["listedNotice_disc"],
                "stock": [str(symbol).zfill(6)],
                "pageSize": 50,
                "pageNum": page,
            }
            payload = _post_json(SZSE_QUERY_URL, headers=headers, payload=body, timeout=timeout)
            data = payload.get("data")
            if data is None:
                raise ValueError("SZSE announcement payload lacks data")
            if not isinstance(data, list):
                raise ValueError("SZSE announcement data is not a list")
            for item in data:
                if not isinstance(item, dict):
                    continue
                raw_code = item.get("secCode") or item.get("securityCode") or symbol
                if isinstance(raw_code, list):
                    raw_code = raw_code[0] if raw_code else symbol
                code = "".join(ch for ch in str(raw_code) if ch.isdigit()).zfill(6)
                if code != str(symbol).zfill(6):
                    continue
                title = re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip()
                published = str(item.get("publishTime") or item.get("publishDate") or "").strip()
                native_id = str(item.get("id") or item.get("annId") or "").strip()
                attach = str(item.get("attachPath") or item.get("url") or "").strip()
                if not title or not published or not native_id or not attach:
                    raise ValueError("SZSE announcement row lacks title/date/document/url identity")
                source_url = (
                    attach
                    if attach.startswith(("http://", "https://"))
                    else f"https://disc.static.szse.cn/download{attach if attach.startswith('/') else '/' + attach}"
                )
                rows.append(
                    {
                        "symbol": str(symbol).zfill(6),
                        "title": title,
                        "publication_time": published,
                        "document_id": native_id,
                        "source_url": source_url,
                    }
                )
            if len(data) < 50:
                break
            page += 1
            if page > 500:
                raise ValueError("SZSE pagination exceeded defensive limit")
    if not rows:
        return pd.DataFrame(columns=["symbol", "title", "publication_time", "document_id", "source_url"])
    out = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "publication_time", "document_id", "source_url"], keep="first"
    )
    return out.sort_values(["publication_time", "document_id"]).reset_index(drop=True)


def normalize_official_announcements(
    frame: pd.DataFrame,
    *,
    spec: OfficialArchiveSpec,
    symbol: str,
    query_start: object,
    query_end: object,
    trading_dates: Iterable[object],
    captured_at: object | None = None,
) -> pd.DataFrame:
    required = {"symbol", "title", "publication_time", "document_id", "source_url"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"official announcement frame missing columns: {sorted(missing)}")
    entity_id = _normalize_entity_id(symbol)
    if not entity_id.endswith(spec.entity_suffix):
        raise ValueError(f"{spec.source_identity} does not apply to {entity_id}")
    x = frame.copy()
    x["symbol"] = x["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    x = x[x["symbol"].eq(str(symbol).zfill(6))].copy()
    if x.empty:
        return pd.DataFrame(
            columns=list(REQUIRED_PIT_COLUMNS) + ["title", "source_url_identity", "captured_at_utc"]
        )
    calendar = _real_trading_calendar(trading_dates)
    start = pd.Timestamp(query_start).normalize()
    end = pd.Timestamp(query_end).normalize()
    captured = pd.Timestamp(captured_at or datetime.now(timezone.utc))
    if captured.tzinfo is None:
        captured = captured.tz_localize("UTC")
    else:
        captured = captured.tz_convert("UTC")

    rows: list[dict[str, object]] = []
    for _, row in x.iterrows():
        publication_value = row["publication_time"]
        publication_ts = pd.Timestamp(pd.to_datetime(publication_value, errors="raise"))
        available_date, availability_rule = _market_available_date(
            publication_value, trading_dates=calendar
        )
        title = re.sub(r"<[^>]+>", "", str(row["title"] or "")).strip()
        document_id = str(row["document_id"] or "").strip()
        source_url = str(row["source_url"] or "").strip()
        if not title or not document_id or not source_url:
            raise ValueError("official announcement identity must be complete")
        evidence_type = classify_cninfo_title(title)
        provenance_payload = {
            "source_identity": spec.source_identity,
            "provider": spec.provider,
            "query_url": spec.query_url,
            "query_start": str(start.date()),
            "query_end": str(end.date()),
            "document_url": source_url,
            "document_id": document_id,
            "event_date_semantics": "PUBLICATION_LEVEL_EVENT_DATE",
            "evidence_available_date_semantics": "FIRST_CLOSE_BASED_REAL_TRADING_DATE_KNOWABLE",
            "availability_rule": availability_rule,
            "title_taxonomy_only": True,
        }
        evidence_id = f"{spec.source_identity.lower()}:{document_id}"
        rows.append(
            {
                "evidence_id": evidence_id,
                "entity_id": entity_id,
                "evidence_type": evidence_type,
                "event_date": publication_ts.normalize(),
                "evidence_available_date": available_date,
                "source_identity": spec.source_identity,
                "provider": spec.provider,
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}",
                "provenance": json.dumps(provenance_payload, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(
                    {
                        "source_identity": spec.source_identity,
                        "entity_id": entity_id,
                        "document_id": document_id,
                        "title": title,
                        "event_date": publication_ts.normalize().isoformat(),
                        "evidence_available_date": available_date.isoformat(),
                        "source_url": source_url,
                    }
                ),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "title": title,
                "source_url_identity": source_url,
                "captured_at_utc": captured.isoformat(),
            }
        )
    return validate_materialized_pit_records(pd.DataFrame(rows))


def materialize_official_archive(
    symbols: Iterable[str],
    *,
    spec: OfficialArchiveSpec,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    fetcher: Callable[..., pd.DataFrame],
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> PitMaterializationResult:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    calendar = _real_trading_calendar(trading_dates)
    captured_at = datetime.now(timezone.utc)
    applicable = sorted(
        {
            str(symbol).zfill(6)
            for symbol in symbols
            if _normalize_entity_id(symbol).endswith(spec.entity_suffix)
        }
    )
    if not applicable:
        raise ValueError(f"no symbols apply to {spec.source_identity}")
    parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for symbol in applicable:
        entity_id = _normalize_entity_id(symbol)
        try:
            raw = call_with_bounded_network_retry(
                lambda: fetcher(symbol=symbol, start_date=start, end_date=end),
                attempts=retry_attempts,
                backoff_seconds=retry_backoff_seconds,
            )
            normalized = normalize_official_announcements(
                raw,
                spec=spec,
                symbol=symbol,
                query_start=start,
                query_end=end,
                trading_dates=calendar,
                captured_at=captured_at,
            )
            if len(normalized):
                parts.append(normalized)
            coverage_rows.append(
                {
                    "source_identity": spec.source_identity,
                    "entity_id": entity_id,
                    "coverage_start": start,
                    "coverage_end": end,
                    "query_status": "COMPLETE_WINDOW",
                    "records": int(len(normalized)),
                    "captured_at_utc": captured_at.isoformat(),
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "source_identity": spec.source_identity,
                    "entity_id": entity_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            coverage_rows.append(
                {
                    "source_identity": spec.source_identity,
                    "entity_id": entity_id,
                    "coverage_start": start,
                    "coverage_end": end,
                    "query_status": "FAILED",
                    "records": 0,
                    "captured_at_utc": captured_at.isoformat(),
                }
            )
    records = (
        validate_materialized_pit_records(pd.concat(parts, ignore_index=True, sort=False))
        if parts
        else pd.DataFrame(
            columns=list(REQUIRED_PIT_COLUMNS) + ["title", "source_url_identity", "captured_at_utc"]
        )
    )
    coverage = pd.DataFrame(coverage_rows)
    summary = {
        "status": "MATERIALIZED_PARTIAL" if len(records) else "NO_RECORDS_MATERIALIZED",
        "source_identity": spec.source_identity,
        "provider": spec.provider,
        "query_url": spec.query_url,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "symbols": int(len(applicable)),
        "complete_symbol_queries": int(coverage["query_status"].eq("COMPLETE_WINDOW").sum()),
        "failed_symbol_queries": int(coverage["query_status"].eq("FAILED").sum()),
        "materialized_records": int(len(records)),
        "captured_at_utc": captured_at.isoformat(),
        "market_date_alignment": "REAL_TRADING_CALENDAR_CLOSE_BASED",
        "materialization_identity": _stable_hash(
            {
                "source": spec.source_identity,
                "start": str(start.date()),
                "end": str(end.date()),
                "symbols": applicable,
                "record_ids": sorted(records["evidence_id"].astype(str).tolist()) if len(records) else [],
            }
        ),
        "search_results_are_not_canonical_evidence": True,
        "future_prices_or_returns_used": False,
        "price_path_used_for_cause_label": False,
    }
    return PitMaterializationResult(
        records=records,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["source_identity", "entity_id", "error"]),
        summary=summary,
    )


def materialize_sse_archive(
    symbols: Iterable[str],
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    fetcher: Callable[..., pd.DataFrame] = fetch_sse_announcements,
) -> PitMaterializationResult:
    return materialize_official_archive(
        symbols,
        spec=SSE_SPEC,
        start_date=start_date,
        end_date=end_date,
        trading_dates=trading_dates,
        fetcher=fetcher,
    )


def materialize_szse_archive(
    symbols: Iterable[str],
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    fetcher: Callable[..., pd.DataFrame] = fetch_szse_announcements,
) -> PitMaterializationResult:
    return materialize_official_archive(
        symbols,
        spec=SZSE_SPEC,
        start_date=start_date,
        end_date=end_date,
        trading_dates=trading_dates,
        fetcher=fetcher,
    )


__all__ = [
    "SSE_SOURCE_ID",
    "SSE_PROVIDER",
    "SSE_QUERY_URL",
    "SZSE_SOURCE_ID",
    "SZSE_PROVIDER",
    "SZSE_QUERY_URL",
    "OfficialArchiveSpec",
    "SSE_SPEC",
    "SZSE_SPEC",
    "fetch_sse_announcements",
    "fetch_szse_announcements",
    "normalize_official_announcements",
    "materialize_official_archive",
    "materialize_sse_archive",
    "materialize_szse_archive",
]
