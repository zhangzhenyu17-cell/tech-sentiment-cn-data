from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .universe import infer_board, normalize_symbol


@dataclass(frozen=True)
class ReconstructionDiagnostics:
    index_code: str
    history_start: pd.Timestamp
    history_end: pd.Timestamp
    anchor_effective_date: pd.Timestamp
    adjustment_dates: int
    adjustment_rows: int
    unique_symbols: int
    segments: int
    expected_constituents: int | None
    min_segment_constituents: int
    max_segment_constituents: int
    variable_constituent_count: bool


def _normalize_optional_symbol(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    return normalize_symbol(text)


def read_anchor_csv(path: str | Path) -> pd.DataFrame:
    anchor = pd.read_csv(path, dtype={"symbol": str})
    if "symbol" not in anchor.columns:
        raise ValueError("anchor CSV must contain a symbol column")
    anchor = anchor.copy()
    anchor["symbol"] = anchor["symbol"].map(normalize_symbol)
    if anchor["symbol"].duplicated().any():
        duplicates = sorted(anchor.loc[anchor["symbol"].duplicated(False), "symbol"].unique())
        raise ValueError(f"anchor contains duplicate symbols: {duplicates}")
    return anchor


def read_adjustments_csv(path: str | Path) -> pd.DataFrame:
    """Read dated in/out changes, including variable-size index events.

    Fixed-size indices normally populate both ``out_symbol`` and ``in_symbol``
    on every row. Variable-size indices may have an unmatched removal or
    addition; represent that with a blank cell on the opposite side. A row may
    never have both sides blank.
    """
    adjustments = pd.read_csv(
        path,
        dtype={"out_symbol": str, "in_symbol": str},
    )
    required = {"effective_date", "out_symbol", "in_symbol"}
    missing = required - set(adjustments.columns)
    if missing:
        raise ValueError(f"adjustments CSV missing required columns: {sorted(missing)}")

    out = adjustments.copy()
    out["effective_date"] = pd.to_datetime(out["effective_date"], errors="raise")
    if "announcement_date" in out.columns:
        out["announcement_date"] = pd.to_datetime(out["announcement_date"], errors="raise")
    out["out_symbol"] = out["out_symbol"].map(_normalize_optional_symbol)
    out["in_symbol"] = out["in_symbol"].map(_normalize_optional_symbol)

    both_missing = out["out_symbol"].isna() & out["in_symbol"].isna()
    if both_missing.any():
        bad = out.loc[both_missing, ["effective_date", "out_symbol", "in_symbol"]]
        raise ValueError(f"adjustment row has neither incoming nor outgoing symbol: {bad.to_dict('records')}")

    both_present = out["out_symbol"].notna() & out["in_symbol"].notna()
    same_symbol = both_present & (out["out_symbol"] == out["in_symbol"])
    if same_symbol.any():
        bad = out.loc[same_symbol, ["effective_date", "out_symbol"]]
        raise ValueError(f"adjustment contains identical in/out symbol: {bad.to_dict('records')}")

    for effective_date, group in out.groupby("effective_date"):
        outgoing = group["out_symbol"].dropna()
        incoming = group["in_symbol"].dropna()
        if outgoing.duplicated().any() or incoming.duplicated().any():
            raise ValueError(f"duplicate in/out symbol within adjustment date {effective_date.date()}")

    return out.sort_values(
        ["effective_date", "out_symbol", "in_symbol"],
        na_position="last",
    ).reset_index(drop=True)


def _append_segment(
    rows: list[dict[str, object]],
    symbols: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    segment_id: int,
) -> None:
    if start > end:
        return
    for symbol in sorted(symbols):
        rows.append(
            {
                "segment_id": segment_id,
                "symbol": symbol,
                "effective_start": start,
                "effective_end": end,
            }
        )


def _compact_intervals(segments: pd.DataFrame) -> pd.DataFrame:
    """Merge calendar-adjacent membership segments for the same symbol."""
    compact: list[dict[str, object]] = []
    for symbol, group in segments.sort_values(["symbol", "effective_start"]).groupby("symbol"):
        current_start: pd.Timestamp | None = None
        current_end: pd.Timestamp | None = None
        for row in group.itertuples(index=False):
            start = pd.Timestamp(row.effective_start)
            end = pd.Timestamp(row.effective_end)
            if current_start is None:
                current_start, current_end = start, end
                continue
            assert current_end is not None
            if start <= current_end + pd.Timedelta(days=1):
                current_end = max(current_end, end)
            else:
                compact.append(
                    {"symbol": symbol, "effective_start": current_start, "effective_end": current_end}
                )
                current_start, current_end = start, end
        if current_start is not None and current_end is not None:
            compact.append(
                {"symbol": symbol, "effective_start": current_start, "effective_end": current_end}
            )
    return pd.DataFrame(compact)


def reconstruct_index_history(
    anchor: pd.DataFrame,
    adjustments: pd.DataFrame,
    *,
    history_start: str | pd.Timestamp,
    history_end: str | pd.Timestamp,
    anchor_effective_date: str | pd.Timestamp | None = None,
    expected_constituents: int | None = 50,
    index_code: str = "000688",
    limit_pct: float = 20.0,
) -> tuple[pd.DataFrame, pd.DataFrame, ReconstructionDiagnostics]:
    """Reconstruct point-in-time membership by reversing dated adjustments.

    ``anchor`` must represent membership immediately *after* the most recent
    adjustment included in ``adjustments``. For each adjustment date, the
    post-adjustment set is recorded, then the event is reversed::

        pre_set = (post_set - incoming) union outgoing

    When ``expected_constituents`` is an integer, every segment must contain
    exactly that many symbols. This remains the fail-closed mode used by fixed
    size indices such as STAR50 and ChiNext50.

    When ``expected_constituents`` is ``None``, unbalanced dated changes are
    permitted and segment size may vary. Blank ``in_symbol`` / ``out_symbol``
    cells represent unmatched removals / additions. Membership consistency is
    still validated at every reversal, and no segment may be empty.

    Returns:
      1. compact symbol membership intervals for use by the analysis pipeline;
      2. full segment snapshots for audit/validation;
      3. reconstruction diagnostics.
    """
    start = pd.Timestamp(history_start).normalize()
    end = pd.Timestamp(history_end).normalize()
    if start > end:
        raise ValueError("history_start must be <= history_end")
    if expected_constituents is not None and expected_constituents <= 0:
        raise ValueError("expected_constituents must be > 0 or None")
    if not 0 < float(limit_pct) <= 100:
        raise ValueError("limit_pct must be in (0, 100]")

    anchor_symbols = {normalize_symbol(v) for v in anchor["symbol"]}
    if not anchor_symbols:
        raise ValueError("anchor must contain at least one symbol")
    if expected_constituents is not None and len(anchor_symbols) != expected_constituents:
        raise ValueError(
            f"anchor has {len(anchor_symbols)} unique symbols; expected {expected_constituents}"
        )

    adj = adjustments.copy()
    adj["effective_date"] = pd.to_datetime(adj["effective_date"], errors="raise").dt.normalize()
    if adj.empty:
        raise ValueError("at least one adjustment is required for historical reconstruction")
    if "out_symbol" not in adj.columns or "in_symbol" not in adj.columns:
        raise ValueError("adjustments must contain out_symbol and in_symbol columns")
    adj["out_symbol"] = adj["out_symbol"].map(_normalize_optional_symbol)
    adj["in_symbol"] = adj["in_symbol"].map(_normalize_optional_symbol)
    if (adj["out_symbol"].isna() & adj["in_symbol"].isna()).any():
        raise ValueError("adjustments contain a row with neither incoming nor outgoing symbol")

    latest_adjustment = pd.Timestamp(adj["effective_date"].max())
    anchor_date = (
        pd.Timestamp(anchor_effective_date).normalize()
        if anchor_effective_date is not None
        else latest_adjustment
    )
    if anchor_date != latest_adjustment:
        raise ValueError(
            f"anchor_effective_date {anchor_date.date()} must match latest adjustment "
            f"{latest_adjustment.date()} for reverse reconstruction"
        )
    if end < anchor_date:
        raise ValueError("history_end cannot be earlier than the anchor effective date")

    current = set(anchor_symbols)
    segment_end = end
    rows: list[dict[str, object]] = []
    segment_id = 0

    relevant_dates = [d for d in sorted(adj["effective_date"].unique(), reverse=True) if d >= start]
    for raw_date in relevant_dates:
        effective_date = pd.Timestamp(raw_date)
        group = adj[adj["effective_date"] == effective_date]
        incoming = set(group["in_symbol"].dropna().astype(str))
        outgoing = set(group["out_symbol"].dropna().astype(str))

        missing_incoming = incoming - current
        unexpected_outgoing = outgoing & current
        if missing_incoming:
            raise ValueError(
                f"{effective_date.date()}: incoming symbols absent from post-adjustment set: "
                f"{sorted(missing_incoming)}"
            )
        if unexpected_outgoing:
            raise ValueError(
                f"{effective_date.date()}: outgoing symbols still present in post-adjustment set: "
                f"{sorted(unexpected_outgoing)}"
            )

        segment_id += 1
        _append_segment(
            rows,
            current,
            effective_date,
            segment_end,
            segment_id=segment_id,
        )

        previous = (current - incoming) | outgoing
        if not previous:
            raise ValueError(f"{effective_date.date()}: reversing adjustment produced an empty index")
        if expected_constituents is not None and len(previous) != expected_constituents:
            raise ValueError(
                f"{effective_date.date()}: reversing adjustment produced {len(previous)} constituents; "
                f"expected {expected_constituents}"
            )
        current = previous
        segment_end = effective_date - pd.Timedelta(days=1)

    if segment_end >= start:
        segment_id += 1
        _append_segment(rows, current, start, segment_end, segment_id=segment_id)

    segments = pd.DataFrame(rows)
    if segments.empty:
        raise ValueError("reconstruction produced no membership segments")

    counts = segments.groupby("segment_id")["symbol"].nunique()
    if expected_constituents is not None:
        if not (counts == expected_constituents).all():
            raise ValueError(f"segment constituent counts invalid: {counts.to_dict()}")
    elif (counts <= 0).any():
        raise ValueError(f"variable-size reconstruction has empty segment: {counts.to_dict()}")

    compact = _compact_intervals(segments)
    compact["universe_mode"] = "point_in_time"
    compact["source_index"] = str(index_code).zfill(6)
    compact["board"] = compact["symbol"].map(infer_board)
    compact["limit_pct"] = float(limit_pct)
    compact = compact.sort_values(["effective_start", "symbol"]).reset_index(drop=True)

    min_count = int(counts.min())
    max_count = int(counts.max())
    diagnostics = ReconstructionDiagnostics(
        index_code=str(index_code).zfill(6),
        history_start=start,
        history_end=end,
        anchor_effective_date=anchor_date,
        adjustment_dates=int(adj["effective_date"].nunique()),
        adjustment_rows=int(len(adj)),
        unique_symbols=int(compact["symbol"].nunique()),
        segments=int(segments["segment_id"].nunique()),
        expected_constituents=expected_constituents,
        min_segment_constituents=min_count,
        max_segment_constituents=max_count,
        variable_constituent_count=min_count != max_count,
    )
    return compact, segments, diagnostics


def write_reconstruction(
    anchor_path: str | Path,
    adjustments_path: str | Path,
    output_path: str | Path,
    *,
    history_start: str,
    history_end: str,
    anchor_effective_date: str,
    expected_constituents: int | None = 50,
    index_code: str = "000688",
    limit_pct: float = 20.0,
) -> ReconstructionDiagnostics:
    anchor = read_anchor_csv(anchor_path)
    adjustments = read_adjustments_csv(adjustments_path)
    compact, _, diagnostics = reconstruct_index_history(
        anchor,
        adjustments,
        history_start=history_start,
        history_end=history_end,
        anchor_effective_date=anchor_effective_date,
        expected_constituents=expected_constituents,
        index_code=index_code,
        limit_pct=limit_pct,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    compact.to_csv(output, index=False, date_format="%Y-%m-%d")
    return diagnostics

