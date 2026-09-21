from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

PUBLIC_CONTRACT_ID = "ipo_aftermarket_public_v1"
BOARDS = {"SSE_MAIN", "SZSE_MAIN", "STAR", "CHINEXT", "BSE"}

METADATA_COLUMNS = [
    "symbol",
    "name",
    "board",
    "listing_date",
    "issue_price",
    "issue_pe",
    "industry_pe",
    "issue_total_raw",
    "metadata_provider",
]
PRICE_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "turnover",
    "board",
    "provider",
]
UNLOCK_COLUMNS = ["symbol", "unlock_date", "unlock_type", "provider"]
UNLOCK_STATUS_COLUMNS = ["symbol", "status", "provider", "error"]

_FORBIDDEN_PUBLIC_TOKENS = (
    "candidate",
    "signal",
    "research_verdict",
    "mae",
    "mfe",
    "max_drawdown",
    "benchmark_relative_return",
    "aftermarket_return",
    "underpricing_return",
    "portfolio",
    "holding",
)


@dataclass(frozen=True)
class PublicBundleAudit:
    metadata_rows: int
    price_rows: int
    unlock_rows: int
    symbols: int
    price_symbols: int
    price_symbol_coverage: float


def infer_ipo_board(symbol: object) -> str | None:
    code = "".join(ch for ch in str(symbol).strip() if ch.isdigit())[-6:].zfill(6)
    if code.startswith(("688", "689")):
        return "STAR"
    if code.startswith(("300", "301", "302")):
        return "CHINEXT"
    if code.startswith(("600", "601", "603", "605")):
        return "SSE_MAIN"
    if code.startswith(("000", "001", "002", "003")):
        return "SZSE_MAIN"
    if code.startswith(("4", "8", "92")):
        return "BSE"
    return None


def _find_column(frame: pd.DataFrame, names: Iterable[str], *, required: bool = False) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    if required:
        raise ValueError(f"required provider column missing; expected one of {list(names)}")
    return None


def normalize_ipo_metadata(raw: pd.DataFrame, *, provider: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError("IPO metadata provider returned no rows")
    symbol_col = _find_column(raw, ("股票代码", "证券代码", "代码"), required=True)
    name_col = _find_column(raw, ("股票简称", "证券简称", "名称"))
    issue_price_col = _find_column(raw, ("发行价", "发行价格"), required=True)
    listing_col = _find_column(raw, ("上市日期", "上市时间"), required=True)
    issue_pe_col = _find_column(raw, ("发行市盈率", "发行PE", "发行 P/E"))
    industry_pe_col = _find_column(raw, ("行业市盈率", "行业PE", "行业 P/E"))
    issue_total_col = _find_column(raw, ("发行总数", "发行数量", "发行量"))

    out = pd.DataFrame()
    out["symbol"] = (
        raw[symbol_col]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.extract(r"(\d+)", expand=False)
        .str[-6:]
        .str.zfill(6)
    )
    out["name"] = raw[name_col].astype(str).str.strip() if name_col else ""
    out["board"] = out["symbol"].map(infer_ipo_board)
    out["listing_date"] = pd.to_datetime(raw[listing_col], errors="coerce").dt.normalize()
    out["issue_price"] = pd.to_numeric(raw[issue_price_col], errors="coerce")
    out["issue_pe"] = (
        pd.to_numeric(raw[issue_pe_col], errors="coerce") if issue_pe_col else pd.NA
    )
    out["industry_pe"] = (
        pd.to_numeric(raw[industry_pe_col], errors="coerce") if industry_pe_col else pd.NA
    )
    out["issue_total_raw"] = raw[issue_total_col].astype(str).str.strip() if issue_total_col else ""
    out["metadata_provider"] = provider

    out = out[
        out["board"].isin(BOARDS)
        & out["listing_date"].notna()
        & out["issue_price"].gt(0)
    ].copy()
    if out.empty:
        raise ValueError("IPO metadata normalization produced no eligible rows")

    conflicts = (
        out.groupby("symbol")[["listing_date", "issue_price"]]
        .nunique(dropna=False)
        .max(axis=1)
        .gt(1)
    )
    if conflicts.any():
        symbols = ", ".join(conflicts[conflicts].index[:10])
        raise ValueError(f"conflicting IPO metadata rows: {symbols}")

    return (
        out.sort_values(["listing_date", "symbol"])
        .drop_duplicates("symbol", keep="last")
        .loc[:, METADATA_COLUMNS]
        .reset_index(drop=True)
    )


def normalize_unlock_queue(raw: pd.DataFrame, *, symbol: str, provider: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=UNLOCK_COLUMNS)
    date_col = _find_column(raw, ("解禁时间", "解禁日期", "上市流通日期"), required=True)
    type_col = _find_column(raw, ("限售股类型", "股份类型", "解禁类型"))
    out = pd.DataFrame(
        {
            "symbol": str(symbol).zfill(6),
            "unlock_date": pd.to_datetime(raw[date_col], errors="coerce").dt.normalize(),
            "unlock_type": raw[type_col].astype(str).str.strip() if type_col else "",
            "provider": provider,
        }
    )
    return (
        out[out["unlock_date"].notna()]
        .sort_values("unlock_date")
        .drop_duplicates(["symbol", "unlock_date", "unlock_type"])
        .reset_index(drop=True)
    )


def allowlist_prices(frame: pd.DataFrame) -> pd.DataFrame:
    missing = {"date", "symbol", "open", "high", "low", "close", "amount", "turnover", "board", "provider"} - set(frame.columns)
    if missing:
        raise ValueError(f"price input missing allowlisted columns: {sorted(missing)}")
    out = frame.loc[:, PRICE_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    for col in ("open", "high", "low", "close", "amount", "turnover"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if out.duplicated(["symbol", "date"]).any():
        raise ValueError("duplicate public IPO price rows")
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def reject_private_columns(frame: pd.DataFrame) -> None:
    for column in frame.columns:
        key = str(column).strip().lower()
        if any(token in key for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError(f"forbidden private/research field in public bundle: {column}")


def validate_public_bundle(
    metadata: pd.DataFrame,
    prices: pd.DataFrame,
    unlocks: pd.DataFrame,
    unlock_status: pd.DataFrame,
    *,
    minimum_price_symbol_coverage: float,
) -> PublicBundleAudit:
    if list(metadata.columns) != METADATA_COLUMNS:
        raise ValueError("IPO metadata allowlist drift")
    if list(prices.columns) != PRICE_COLUMNS:
        raise ValueError("IPO price allowlist drift")
    if list(unlocks.columns) != UNLOCK_COLUMNS:
        raise ValueError("IPO unlock allowlist drift")
    if list(unlock_status.columns) != UNLOCK_STATUS_COLUMNS:
        raise ValueError("IPO unlock-status allowlist drift")
    for frame in (metadata, prices, unlocks, unlock_status):
        reject_private_columns(frame)

    symbols = set(metadata["symbol"].astype(str))
    price_symbols = set(prices["symbol"].astype(str))
    if not price_symbols.issubset(symbols):
        raise ValueError("price bundle contains symbols outside IPO metadata")
    coverage = len(price_symbols) / len(symbols) if symbols else 0.0
    if coverage < minimum_price_symbol_coverage:
        raise ValueError(
            f"IPO price symbol coverage {coverage:.1%} below {minimum_price_symbol_coverage:.1%}"
        )
    if set(unlock_status["symbol"].astype(str)) != symbols:
        raise ValueError("unlock query status must cover every IPO metadata symbol")

    return PublicBundleAudit(
        metadata_rows=len(metadata),
        price_rows=len(prices),
        unlock_rows=len(unlocks),
        symbols=len(symbols),
        price_symbols=len(price_symbols),
        price_symbol_coverage=coverage,
    )
