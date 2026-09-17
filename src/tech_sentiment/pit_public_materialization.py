from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlparse

import pandas as pd


CNINFO_SOURCE_ID = "CNINFO_ANNOUNCEMENT_ARCHIVE"
CNINFO_PROVIDER = "CNINFO"
CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

REQUIRED_PIT_COLUMNS = (
    "evidence_id",
    "entity_id",
    "evidence_type",
    "event_date",
    "evidence_available_date",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "provenance",
    "ingestion_identity",
    "availability_state",
)


@dataclass(frozen=True)
class PitMaterializationResult:
    records: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_entity_id(symbol: object) -> str:
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"entity symbol must resolve to six digits: {symbol}")
    if digits.startswith(("5", "6", "9")):
        return f"{digits}.SH"
    if digits.startswith(("0", "1", "2", "3")):
        return f"{digits}.SZ"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return digits


def _parse_document_identity(url: object) -> tuple[str, str]:
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query)
    announcement = str((query.get("announcementId") or [""])[0]).strip()
    org_id = str((query.get("orgId") or [""])[0]).strip()
    if not announcement:
        raise ValueError("CNINFO announcement link lacks announcementId")
    return announcement, org_id


def _real_trading_calendar(trading_dates: Iterable[object]) -> pd.DatetimeIndex:
    calendar = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    if not len(calendar):
        raise ValueError("real trading calendar cannot be empty")
    return calendar


def _publication_has_precise_clock(value: object) -> bool:
    return bool(re.search(r"(?:^|\s)\d{1,2}:\d{2}(?::\d{2})?(?:\s|$)", str(value).strip()))


def _market_available_date(
    publication_value: object,
    *,
    trading_dates: pd.DatetimeIndex,
) -> tuple[pd.Timestamp, str]:
    """Map a publication timestamp to the first close-based market date that may use it.

    A precise timestamp at or before 15:00 on a real trading day may be used on
    that date. Date-only disclosures, after-close disclosures and non-trading
    day disclosures are conservatively delayed to the next real trading day.
    No next trading day means fail closed rather than same-day backfill.
    """

    publication = pd.Timestamp(pd.to_datetime(publication_value, errors="raise"))
    publication_date = publication.normalize()
    precise = _publication_has_precise_clock(publication_value)
    is_trade_day = publication_date in trading_dates
    at_or_before_close = precise and (
        publication.hour < 15
        or (
            publication.hour == 15
            and publication.minute == 0
            and publication.second == 0
        )
    )
    if is_trade_day and at_or_before_close:
        return publication_date, "PRE_OR_AT_CLOSE_TIMESTAMP_SAME_TRADE_DATE"

    later = trading_dates[trading_dates > publication_date]
    if not len(later):
        raise ValueError(
            "real trading calendar lacks next market date for date-only, after-close, or non-trading-day publication"
        )
    reason = (
        "DATE_ONLY_NEXT_TRADE_DATE"
        if not precise
        else "AFTER_CLOSE_NEXT_TRADE_DATE"
        if is_trade_day
        else "NON_TRADING_DAY_NEXT_TRADE_DATE"
    )
    return pd.Timestamp(later[0]).normalize(), reason


def classify_cninfo_title(title: object) -> str:
    """Classify document type from the disclosure title only.

    This is a public document taxonomy, not a market-direction label. No price,
    return, model output, or hindsight information is used.
    """

    text = re.sub(r"<[^>]+>", "", str(title or "")).strip()
    if not text:
        return "MAJOR_EVENT"
    if any(token in text for token in ("更正", "修订", "补充更正")) and any(
        token in text for token in ("年度报告", "半年度报告", "季度报告", "财务报告")
    ):
        return "FINANCIAL_RESTATEMENT"
    if any(token in text for token in ("年度报告", "半年度报告", "一季度报告", "三季度报告", "季度报告")):
        return "FINANCIAL_REPORT_ANNOUNCEMENT"
    if any(token in text for token in ("业绩预告", "业绩快报", "盈利预测", "业绩修正")):
        return "ISSUER_EARNINGS_FORECAST"
    if any(token in text for token in ("诉讼", "仲裁")):
        return "LITIGATION_EVENT"
    if any(token in text for token in ("债务逾期", "违约", "信用风险", "债券违约")):
        return "CREDIT_EVENT"
    if any(token in text for token in ("中标", "重大合同", "订单")):
        return "MAJOR_ORDER"
    if any(token in text for token in ("许可", "授权", "合作协议", "战略合作", "商务合作")):
        return "BD_EVENT"
    if any(
        token in text
        for token in (
            "风险提示",
            "退市",
            "立案",
            "处罚",
            "重大损失",
            "重大减值",
            "重大不利",
            "经营异常",
            "暂停生产",
            "终止临床",
            "临床失败",
        )
    ):
        return "MAJOR_NEGATIVE_EVENT"
    return "MAJOR_EVENT"


def normalize_cninfo_announcements(
    frame: pd.DataFrame,
    *,
    symbol: str,
    query_start: object,
    query_end: object,
    trading_dates: Iterable[object],
    captured_at: object | None = None,
) -> pd.DataFrame:
    required = {"代码", "简称", "公告标题", "公告时间", "公告链接"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CNINFO announcement frame missing columns: {sorted(missing)}")
    entity_id = _normalize_entity_id(symbol)
    x = frame.copy()
    x["代码"] = x["代码"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    x = x[x["代码"].eq(str(symbol).zfill(6))].copy()
    if x.empty:
        return pd.DataFrame(
            columns=list(REQUIRED_PIT_COLUMNS)
            + ["title", "source_url_identity", "captured_at_utc"]
        )

    calendar = _real_trading_calendar(trading_dates)
    captured = pd.Timestamp(captured_at or datetime.now(timezone.utc))
    if captured.tzinfo is None:
        captured = captured.tz_localize("UTC")
    else:
        captured = captured.tz_convert("UTC")
    start = pd.Timestamp(query_start).normalize()
    end = pd.Timestamp(query_end).normalize()
    if end < start:
        raise ValueError("query_end must not precede query_start")

    rows: list[dict[str, object]] = []
    for _, row in x.iterrows():
        publication_ts = pd.Timestamp(pd.to_datetime(row["公告时间"], errors="raise"))
        event_date = publication_ts.normalize()
        available_date, availability_rule = _market_available_date(
            row["公告时间"], trading_dates=calendar
        )
        document_id, org_id = _parse_document_identity(row["公告链接"])
        title = re.sub(r"<[^>]+>", "", str(row["公告标题"])).strip()
        evidence_type = classify_cninfo_title(title)
        source_url = str(row["公告链接"]).strip()
        provenance_payload = {
            "source_identity": CNINFO_SOURCE_ID,
            "provider": CNINFO_PROVIDER,
            "query_url": CNINFO_QUERY_URL,
            "query_start": str(start.date()),
            "query_end": str(end.date()),
            "announcement_url": source_url,
            "announcement_id": document_id,
            "org_id": org_id,
            "event_date_semantics": "PUBLICATION_LEVEL_EVENT_DATE",
            "evidence_available_date_semantics": "FIRST_CLOSE_BASED_REAL_TRADING_DATE_KNOWABLE",
            "availability_rule": availability_rule,
            "publication_clock_precise": _publication_has_precise_clock(row["公告时间"]),
            "title_taxonomy_only": True,
        }
        ingestion_identity = _stable_hash(
            {
                "entity_id": entity_id,
                "document_id": document_id,
                "title": title,
                "event_date": event_date.isoformat(),
                "evidence_available_date": available_date.isoformat(),
                "source": CNINFO_SOURCE_ID,
            }
        )
        rows.append(
            {
                "evidence_id": f"cninfo:{document_id}",
                "entity_id": entity_id,
                "evidence_type": evidence_type,
                "event_date": event_date,
                "evidence_available_date": available_date,
                "source_identity": CNINFO_SOURCE_ID,
                "provider": CNINFO_PROVIDER,
                "document_id": document_id,
                "revision_id": f"DOCUMENT:{document_id}",
                "provenance": json.dumps(provenance_payload, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": ingestion_identity,
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "title": title,
                "source_url_identity": source_url,
                "captured_at_utc": captured.isoformat(),
            }
        )
    out = pd.DataFrame(rows)
    return validate_materialized_pit_records(out)


def validate_materialized_pit_records(records: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_PIT_COLUMNS if column not in records.columns]
    if missing:
        raise ValueError(f"PIT materialization missing required columns: {missing}")
    out = records.copy().reset_index(drop=True)
    for column in ("event_date", "evidence_available_date"):
        out[column] = pd.to_datetime(out[column], errors="raise").dt.normalize()
    if (out["evidence_available_date"] < out["event_date"]).any():
        raise ValueError("evidence_available_date cannot precede event_date")
    for column in (
        "evidence_id",
        "entity_id",
        "evidence_type",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "provenance",
        "ingestion_identity",
        "availability_state",
    ):
        if out[column].isna().any() or out[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"PIT materialization requires non-empty {column}")
    if out["evidence_id"].duplicated().any():
        dupes = sorted(out.loc[out["evidence_id"].duplicated(keep=False), "evidence_id"].unique())
        raise ValueError(f"duplicate evidence_id values: {dupes}")
    if out["ingestion_identity"].duplicated().any():
        raise ValueError("duplicate ingestion_identity values")
    allowed = {"HISTORICAL_RECONSTRUCTABLE", "FORWARD_ONLY", "UNAVAILABLE", "DATA_INSUFFICIENT"}
    bad = sorted(set(out["availability_state"].astype(str)) - allowed)
    if bad:
        raise ValueError(f"invalid availability_state values: {bad}")
    return out.sort_values(["evidence_available_date", "entity_id", "evidence_id"]).reset_index(drop=True)


def materialize_cninfo_archive(
    symbols: Iterable[str],
    *,
    start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> PitMaterializationResult:
    if fetcher is None:
        import akshare as ak  # type: ignore

        fetcher = ak.stock_zh_a_disclosure_report_cninfo
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not precede start_date")
    calendar = _real_trading_calendar(trading_dates)
    captured_at = datetime.now(timezone.utc)
    parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    unique_symbols = sorted({str(symbol).zfill(6) for symbol in symbols})
    if not unique_symbols:
        raise ValueError("at least one symbol is required")

    for symbol in unique_symbols:
        entity_id = _normalize_entity_id(symbol)
        try:
            raw = fetcher(
                symbol=symbol,
                market="沪深京",
                keyword="",
                category="",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            normalized = normalize_cninfo_announcements(
                raw,
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
                    "source_identity": CNINFO_SOURCE_ID,
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
                    "source_identity": CNINFO_SOURCE_ID,
                    "entity_id": entity_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            coverage_rows.append(
                {
                    "source_identity": CNINFO_SOURCE_ID,
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
            columns=list(REQUIRED_PIT_COLUMNS)
            + ["title", "source_url_identity", "captured_at_utc"]
        )
    )
    coverage = pd.DataFrame(coverage_rows)
    summary = {
        "status": "MATERIALIZED_PARTIAL" if len(records) else "NO_RECORDS_MATERIALIZED",
        "source_identity": CNINFO_SOURCE_ID,
        "provider": CNINFO_PROVIDER,
        "query_url": CNINFO_QUERY_URL,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "symbols": int(len(unique_symbols)),
        "complete_symbol_queries": int(coverage["query_status"].eq("COMPLETE_WINDOW").sum()),
        "failed_symbol_queries": int(coverage["query_status"].eq("FAILED").sum()),
        "materialized_records": int(len(records)),
        "captured_at_utc": captured_at.isoformat(),
        "market_date_alignment": "REAL_TRADING_CALENDAR_CLOSE_BASED",
        "materialization_identity": _stable_hash(
            {
                "source": CNINFO_SOURCE_ID,
                "start": str(start.date()),
                "end": str(end.date()),
                "symbols": unique_symbols,
                "record_ids": sorted(records["evidence_id"].astype(str).tolist()) if len(records) else [],
                "available_dates": sorted(
                    pd.to_datetime(records["evidence_available_date"]).dt.strftime("%Y-%m-%d").tolist()
                )
                if len(records)
                else [],
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


def major_negative_source_coverage_complete(
    coverage: pd.DataFrame,
    *,
    entity_id: str,
    market_date: object,
    required_source_identities: Iterable[str],
) -> bool:
    """Return coverage completeness only; this does not mean no adverse event exists."""

    required = sorted({str(value) for value in required_source_identities})
    if not required:
        return False
    needed = {"source_identity", "entity_id", "coverage_start", "coverage_end", "query_status"}
    if needed - set(coverage.columns):
        return False
    date = pd.Timestamp(market_date).normalize()
    x = coverage[coverage["entity_id"].astype(str).eq(str(entity_id))].copy()
    if x.empty:
        return False
    x["coverage_start"] = pd.to_datetime(x["coverage_start"], errors="coerce").dt.normalize()
    x["coverage_end"] = pd.to_datetime(x["coverage_end"], errors="coerce").dt.normalize()
    for source in required:
        rows = x[x["source_identity"].eq(source)]
        if rows.empty:
            return False
        ok = rows[
            rows["query_status"].eq("COMPLETE_WINDOW")
            & rows["coverage_start"].le(date)
            & rows["coverage_end"].ge(date)
        ]
        if ok.empty:
            return False
    return True


__all__ = [
    "CNINFO_SOURCE_ID",
    "CNINFO_PROVIDER",
    "CNINFO_QUERY_URL",
    "REQUIRED_PIT_COLUMNS",
    "PitMaterializationResult",
    "classify_cninfo_title",
    "normalize_cninfo_announcements",
    "validate_materialized_pit_records",
    "materialize_cninfo_archive",
    "major_negative_source_coverage_complete",
]
