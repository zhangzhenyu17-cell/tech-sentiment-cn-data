from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


INNOVATION_DRUG_INDEX = "931152"
DESIGN_START = pd.Timestamp("2019-04-22")
DESIGN_END = pd.Timestamp("2023-12-31")
STOCK_WARMUP_START = pd.Timestamp("2018-01-01")
STOCK_ADJUSTMENT = "qfq"


@dataclass(frozen=True)
class DesignInputAudit:
    eligible_for_design_input: bool
    stock_rows: int
    stock_symbols: int
    index_rows: int
    missing_symbols: tuple[str, ...]
    errors: tuple[str, ...]


def audit_design_price_inputs(
    prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    requested_symbols: list[str] | tuple[str, ...],
) -> DesignInputAudit:
    """Audit public price inputs without inspecting model events or outcomes.

    Stock history may begin before the design interval solely for rolling-feature
    warm-up. No row after DESIGN_END is allowed. The official index rail must be
    confined to the design interval and identify 931152.
    """

    errors: list[str] = []
    required_price = {"date", "symbol", "close", "pct_chg", "amount", "provider"}
    required_index = {"date", "index_code", "close", "provider"}
    missing_price_cols = required_price - set(prices.columns)
    missing_index_cols = required_index - set(index_prices.columns)
    if missing_price_cols:
        errors.append(f"stock prices missing columns: {sorted(missing_price_cols)}")
    if missing_index_cols:
        errors.append(f"index prices missing columns: {sorted(missing_index_cols)}")

    stock_symbols: set[str] = set()
    if not missing_price_cols and not prices.empty:
        stock = prices.copy()
        stock["date"] = pd.to_datetime(stock["date"], errors="raise").dt.normalize()
        stock["symbol"] = stock["symbol"].astype(str).str.zfill(6)
        stock_symbols = set(stock["symbol"])
        if stock["date"].min() < STOCK_WARMUP_START:
            errors.append("stock history begins before frozen warm-up start")
        if stock["date"].max() > DESIGN_END:
            errors.append("stock history crosses untouched holdout boundary")
        if stock.duplicated(subset=["date", "symbol"]).any():
            errors.append("duplicate stock price rows")
    elif prices.empty:
        errors.append("stock price input is empty")

    requested = {str(symbol).zfill(6) for symbol in requested_symbols}
    missing_symbols = tuple(sorted(requested - stock_symbols))
    if missing_symbols:
        errors.append(f"missing stock histories: {len(missing_symbols)}")

    if not missing_index_cols and not index_prices.empty:
        index = index_prices.copy()
        index["date"] = pd.to_datetime(index["date"], errors="raise").dt.normalize()
        codes = set(index["index_code"].astype(str).str.zfill(6))
        if codes != {INNOVATION_DRUG_INDEX}:
            errors.append(f"unexpected index codes: {sorted(codes)}")
        if index["date"].min() < DESIGN_START:
            errors.append("index rail begins before official 931152 design start")
        if index["date"].max() > DESIGN_END:
            errors.append("index rail crosses untouched holdout boundary")
        if index["date"].duplicated().any():
            errors.append("duplicate index price dates")
    elif index_prices.empty:
        errors.append("index price input is empty")

    return DesignInputAudit(
        eligible_for_design_input=not errors,
        stock_rows=int(len(prices)),
        stock_symbols=len(stock_symbols),
        index_rows=int(len(index_prices)),
        missing_symbols=missing_symbols,
        errors=tuple(errors),
    )
