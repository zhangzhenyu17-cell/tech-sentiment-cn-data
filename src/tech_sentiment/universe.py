from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class UniverseDiagnostics:
    mode: str
    symbols: int
    membership_rows: int
    has_effective_ranges: bool
    has_point_in_time_history: bool


def normalize_symbol(value: object) -> str:
    """Normalize an A-share security code to six digits."""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid symbol: {value!r}")
    return digits[-6:].zfill(6)


def infer_board(symbol: str) -> str:
    """Infer the trading board from a six-digit A-share code.

    The mapping is intentionally conservative. ``limit_pct`` supplied by the
    universe should be preferred whenever historical ST status or special rules
    matter.
    """
    code = normalize_symbol(symbol)
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301", "302")):
        return "chinext"
    if code.startswith(("4", "8", "92")):
        return "beijing"
    return "main"


def normalize_universe(universe: pd.DataFrame) -> pd.DataFrame:
    """Normalize a current-snapshot or point-in-time membership table.

    Required column:
      - ``symbol``

    Optional point-in-time columns:
      - ``effective_start`` (inclusive)
      - ``effective_end`` (inclusive)

    Optional metadata columns such as ``board``, ``limit_pct``, ``name`` and
    ``source_index`` are preserved.
    """
    if "symbol" not in universe.columns:
        raise ValueError("universe must contain a 'symbol' column")

    out = universe.copy()
    out["symbol"] = out["symbol"].map(normalize_symbol)

    if "board" not in out.columns:
        out["board"] = out["symbol"].map(infer_board)
    else:
        missing_board = out["board"].isna() | (out["board"].astype(str).str.strip() == "")
        out.loc[missing_board, "board"] = out.loc[missing_board, "symbol"].map(infer_board)

    for column in ("effective_start", "effective_end"):
        if column in out.columns:
            out[column] = pd.to_datetime(out[column], errors="coerce")

    if "universe_mode" not in out.columns:
        has_ranges = "effective_start" in out.columns or "effective_end" in out.columns
        out["universe_mode"] = "point_in_time" if has_ranges else "current_snapshot"

    return out


def universe_diagnostics(universe: pd.DataFrame) -> UniverseDiagnostics:
    normalized = normalize_universe(universe)
    has_ranges = "effective_start" in normalized.columns or "effective_end" in normalized.columns
    modes = sorted(set(normalized["universe_mode"].astype(str)))
    mode = modes[0] if len(modes) == 1 else "mixed"
    is_genuine_pit = has_ranges and bool(modes) and all(m == "point_in_time" for m in modes)
    return UniverseDiagnostics(
        mode=mode,
        symbols=int(normalized["symbol"].nunique()),
        membership_rows=int(len(normalized)),
        has_effective_ranges=has_ranges,
        has_point_in_time_history=is_genuine_pit,
    )


def current_snapshot_to_interval(
    universe: pd.DataFrame,
    *,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Expand a current constituent snapshot across a research period.

    This is deliberately labelled ``current_snapshot`` because applying today's
    membership to past dates creates survivorship / constituent-selection bias.
    It is appropriate only for fast pipeline validation, never for a formal
    historical performance claim.
    """
    out = normalize_universe(universe)
    out["effective_start"] = pd.Timestamp(start_date)
    out["effective_end"] = pd.Timestamp(end_date) if end_date is not None else pd.NaT
    out["universe_mode"] = "current_snapshot"
    return out


def _coalesce_column(frame: pd.DataFrame, base: str) -> pd.Series:
    left = f"{base}_price"
    right = f"{base}_universe"
    if left in frame.columns and right in frame.columns:
        return frame[left].combine_first(frame[right])
    if left in frame.columns:
        return frame[left]
    if right in frame.columns:
        return frame[right]
    return pd.Series(pd.NA, index=frame.index)


def apply_universe_membership(prices: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Filter stock-day prices using inclusive point-in-time membership ranges.

    A current snapshot without effective dates is treated as a simple symbol
    filter. A history-aware universe may contain multiple non-overlapping rows
    for the same symbol, allowing securities to enter, leave, and re-enter.
    """
    required = {"date", "symbol"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"prices missing required columns: {sorted(missing)}")

    price_df = prices.copy()
    price_df["date"] = pd.to_datetime(price_df["date"], errors="raise")
    price_df["symbol"] = price_df["symbol"].map(normalize_symbol)
    uni = normalize_universe(universe)

    has_ranges = "effective_start" in uni.columns or "effective_end" in uni.columns
    metadata = [
        c
        for c in ("board", "limit_pct", "name", "source_index", "universe_mode")
        if c in uni.columns
    ]

    if not has_ranges:
        allowed = set(uni["symbol"])
        out = price_df[price_df["symbol"].isin(allowed)].copy()
        meta = uni.drop_duplicates("symbol").set_index("symbol")
        for column in metadata:
            if column not in out.columns:
                out[column] = out["symbol"].map(meta[column])
        return out.sort_values(["date", "symbol"]).reset_index(drop=True)

    if "effective_start" not in uni.columns:
        uni["effective_start"] = pd.NaT
    if "effective_end" not in uni.columns:
        uni["effective_end"] = pd.NaT

    merge_columns = ["symbol", "effective_start", "effective_end", *metadata]
    merged = price_df.merge(
        uni[merge_columns],
        on="symbol",
        how="inner",
        suffixes=("_price", "_universe"),
    )
    start_ok = merged["effective_start"].isna() | (merged["date"] >= merged["effective_start"])
    end_ok = merged["effective_end"].isna() | (merged["date"] <= merged["effective_end"])
    merged = merged[start_ok & end_ok].copy()

    for column in ("board", "limit_pct"):
        price_col = f"{column}_price"
        universe_col = f"{column}_universe"
        if price_col in merged.columns or universe_col in merged.columns:
            merged[column] = _coalesce_column(merged, column)
            merged = merged.drop(
                columns=[c for c in (price_col, universe_col) if c in merged.columns]
            )

    merged = merged.drop_duplicates(subset=["date", "symbol"], keep="last")
    return merged.sort_values(["date", "symbol"]).reset_index(drop=True)


def read_universe_csv(path: str) -> pd.DataFrame:
    return normalize_universe(pd.read_csv(path, dtype={"symbol": str}))


def symbols_from_universe(universe: pd.DataFrame) -> list[str]:
    normalized = normalize_universe(universe)
    return sorted(set(normalized["symbol"]))

