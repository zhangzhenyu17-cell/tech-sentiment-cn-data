from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd

from tech_sentiment.capital_input_data import qualify_trailing_etf_coverage


EYESOFBLUE_ARCHIVE_SOURCE_ID = "GITHUB_EYESOFBLUE_SSE_ETF_ARCHIVE"
EYESOFBLUE_ARCHIVE_REPO = "eyesofblue/trade-sse_etf_data"
EYESOFBLUE_ARCHIVE_COMMIT = "8759e44647832ecff4141f96f435036d19482174"
EYESOFBLUE_ARCHIVE_PATH = "data/curated/sse_etf_shares.csv"
EYESOFBLUE_ARCHIVE_URL = (
    "https://github.com/eyesofblue/trade-sse_etf_data/blob/"
    f"{EYESOFBLUE_ARCHIVE_COMMIT}/{EYESOFBLUE_ARCHIVE_PATH}"
)

ZXTCC_METHOD_SOURCE_ID = "GITHUB_ZXTCC_SSE_ETF_METHOD_REFERENCE"
ZXTCC_METHOD_REPO = "zxtcc/sse-etf-share"
ZXTCC_METHOD_COMMIT = "0f9795f23881bbb3199001247c9b2d80d3bb1fd4"
ZXTCC_METHOD_URL = (
    "https://github.com/zxtcc/sse-etf-share/tree/"
    f"{ZXTCC_METHOD_COMMIT}"
)

SSE_ETF_SCALE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_ETF_SCALE_SQL_ID = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SSE_ETF_SCALE_FIELD = "TOT_VOL"

PUBLIC_ETF_ARCHIVE_REGISTRY = {
    EYESOFBLUE_ARCHIVE_SOURCE_ID: {
        "role": "CANDIDATE_ARCHIVE_DATA",
        "repository": EYESOFBLUE_ARCHIVE_REPO,
        "commit": EYESOFBLUE_ARCHIVE_COMMIT,
        "path": EYESOFBLUE_ARCHIVE_PATH,
        "source_url": EYESOFBLUE_ARCHIVE_URL,
        "upstream_provider": "Shanghai Stock Exchange",
        "provider_interface": SSE_ETF_SCALE_QUERY_URL,
        "provider_sql_id": SSE_ETF_SCALE_SQL_ID,
        "provider_field": SSE_ETF_SCALE_FIELD,
        "raw_unit": "10k_share",
    },
    ZXTCC_METHOD_SOURCE_ID: {
        "role": "METHOD_REFERENCE_ONLY",
        "repository": ZXTCC_METHOD_REPO,
        "commit": ZXTCC_METHOD_COMMIT,
        "source_url": ZXTCC_METHOD_URL,
        "upstream_provider": "Shanghai Stock Exchange",
        "provider_interface": SSE_ETF_SCALE_QUERY_URL,
        "provider_sql_id": SSE_ETF_SCALE_SQL_ID,
        "provider_field": SSE_ETF_SCALE_FIELD,
        "raw_unit": "10k_share",
        "versioned_588000_archive": False,
    },
}


@dataclass(frozen=True)
class EtfArchiveAuditResult:
    normalized: pd.DataFrame
    coverage: pd.DataFrame
    summary: dict[str, object]


def _normalize_code(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _shares_from_10k(value: object) -> int:
    text = str(value or "").replace(",", "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"archive total_shares_10k is invalid: {value}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"archive total_shares_10k must be positive: {value}")
    shares = number * Decimal("10000")
    if shares != shares.to_integral_value():
        raise ValueError(
            "archive total_shares_10k cannot be represented as whole shares"
        )
    return int(shares)


def normalize_public_etf_share_archive(
    frame: pd.DataFrame,
    *,
    fund_code: str,
    source_identity: str = EYESOFBLUE_ARCHIVE_SOURCE_ID,
) -> pd.DataFrame:
    """Normalize an archived SSE ETF-share CSV without granting evidence status.

    The known public archive stores SSE ``TOT_VOL`` in units of ten-thousand
    shares.  Historical archive presence alone does not establish the original
    point-in-time availability of each row, so the normalized output remains a
    candidate rail and is never marked canonical by this function.
    """

    required = {"trade_date", "exchange", "fund_code", "total_shares_10k"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF archive missing columns: {sorted(missing)}")
    if source_identity not in PUBLIC_ETF_ARCHIVE_REGISTRY:
        raise ValueError(f"unregistered ETF archive source: {source_identity}")
    source = PUBLIC_ETF_ARCHIVE_REGISTRY[source_identity]
    if source["role"] != "CANDIDATE_ARCHIVE_DATA":
        raise ValueError("method-only source cannot be normalized as archive data")

    code = _normalize_code(fund_code)
    if len(code) != 6:
        raise ValueError("fund_code must resolve to 6 digits")

    x = frame.copy()
    x["exchange"] = x["exchange"].astype(str).str.upper().str.strip()
    x["fund_code"] = x["fund_code"].map(_normalize_code)
    x = x[(x["exchange"] == "SSE") & (x["fund_code"] == code)].copy()
    if x.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "fund_code",
                "fund_shares",
                "unit",
                "source_identity",
                "source_url",
                "source_commit",
                "upstream_provider",
                "provider_interface",
                "provider_sql_id",
                "provider_field",
                "archive_fetch_time",
                "point_in_time_status",
                "archive_role",
            ]
        )

    x["date"] = pd.to_datetime(x["trade_date"], errors="raise").dt.normalize()
    x["fund_shares"] = x["total_shares_10k"].map(_shares_from_10k)
    if x["date"].duplicated().any():
        dupes = x.loc[x["date"].duplicated(keep=False), "date"].dt.strftime("%Y-%m-%d")
        raise ValueError(f"ETF archive has duplicate dates for fund: {sorted(set(dupes))}")

    fetched = (
        pd.to_datetime(x["fetched_at"], errors="coerce", utc=True)
        if "fetched_at" in x.columns
        else pd.Series(pd.NaT, index=x.index, dtype="datetime64[ns, UTC]")
    )
    x["archive_fetch_time"] = fetched
    x["unit"] = "share"
    x["source_identity"] = source_identity
    x["source_url"] = source["source_url"]
    x["source_commit"] = source["commit"]
    x["upstream_provider"] = source["upstream_provider"]
    x["provider_interface"] = source["provider_interface"]
    x["provider_sql_id"] = source["provider_sql_id"]
    x["provider_field"] = source["provider_field"]
    x["point_in_time_status"] = "NOT_ESTABLISHED_BY_ARCHIVE"
    x["archive_role"] = "CANDIDATE_ONLY"
    return x[
        [
            "date",
            "fund_code",
            "fund_shares",
            "unit",
            "source_identity",
            "source_url",
            "source_commit",
            "upstream_provider",
            "provider_interface",
            "provider_sql_id",
            "provider_field",
            "archive_fetch_time",
            "point_in_time_status",
            "archive_role",
        ]
    ].sort_values("date").reset_index(drop=True)


def audit_public_etf_share_archive(
    frame: pd.DataFrame,
    *,
    trading_dates: Iterable[object],
    fund_code: str,
    window: int = 60,
    min_coverage: float = 0.80,
    source_identity: str = EYESOFBLUE_ARCHIVE_SOURCE_ID,
) -> EtfArchiveAuditResult:
    """Audit archive completeness while keeping it outside canonical evidence.

    This is intentionally a fail-closed audit.  Even an archive with complete
    calendar coverage remains non-canonical until point-in-time provenance is
    independently established and an explicit evidence-qualification decision
    is made elsewhere.
    """

    cal = (
        pd.DatetimeIndex(pd.to_datetime(list(trading_dates), errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    normalized = normalize_public_etf_share_archive(
        frame,
        fund_code=fund_code,
        source_identity=source_identity,
    )
    coverage = qualify_trailing_etf_coverage(
        normalized[["date", "fund_code", "fund_shares"]],
        trading_dates=cal,
        fund_code=fund_code,
        window=window,
        min_coverage=min_coverage,
    )

    observed = coverage[coverage["observed"]]
    first_observed = observed["date"].min() if len(observed) else pd.NaT
    last_observed = observed["date"].max() if len(observed) else pd.NaT
    prehistory_gap_days = (
        int((coverage["date"] < first_observed).sum())
        if pd.notna(first_observed)
        else int(len(coverage))
    )
    posthistory_gap_days = (
        int((coverage["date"] > last_observed).sum())
        if pd.notna(last_observed)
        else 0
    )
    qualified_windows = coverage[coverage["coverage"].notna()]
    latest_trailing = (
        float(qualified_windows.iloc[-1]["coverage"])
        if len(qualified_windows)
        else None
    )
    raw_coverage = float(coverage["observed"].mean()) if len(coverage) else None

    if observed.empty:
        status = "CANDIDATE_ARCHIVE_EMPTY"
    elif prehistory_gap_days:
        status = "CANDIDATE_ARCHIVE_PARTIAL_PREHISTORY"
    elif raw_coverage is not None and raw_coverage < min_coverage:
        status = "CANDIDATE_ARCHIVE_INSUFFICIENT_COVERAGE"
    else:
        status = "CANDIDATE_ARCHIVE_AUDITED_NOT_CANONICAL"

    summary: dict[str, object] = {
        "status": status,
        "source_identity": source_identity,
        "source_commit": PUBLIC_ETF_ARCHIVE_REGISTRY[source_identity]["commit"],
        "fund_code": _normalize_code(fund_code),
        "calendar_start": str(cal.min().date()) if len(cal) else None,
        "calendar_end": str(cal.max().date()) if len(cal) else None,
        "trading_days": int(len(cal)),
        "observed_days": int(coverage["observed"].sum()),
        "raw_coverage": raw_coverage,
        "first_observed_date": (
            str(pd.Timestamp(first_observed).date()) if pd.notna(first_observed) else None
        ),
        "last_observed_date": (
            str(pd.Timestamp(last_observed).date()) if pd.notna(last_observed) else None
        ),
        "prehistory_gap_days": prehistory_gap_days,
        "posthistory_gap_days": posthistory_gap_days,
        "trailing_window": int(window),
        "trailing_min_coverage": float(min_coverage),
        "latest_trailing_coverage": latest_trailing,
        "point_in_time_qualified": False,
        "canonical_input_qualified": False,
        "production_or_model_output": False,
    }
    return EtfArchiveAuditResult(
        normalized=normalized,
        coverage=coverage,
        summary=summary,
    )
