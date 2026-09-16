from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .index_history import read_adjustments_csv, read_anchor_csv, reconstruct_index_history
from .universe import normalize_symbol


@dataclass(frozen=True)
class ProductionAnchor:
    effective_date: pd.Timestamp
    anchor_path: Path
    adjustment_paths: tuple[Path, ...]


def read_anchor_manifest(path: str | Path) -> pd.DataFrame:
    manifest_path = Path(path)
    frame = pd.read_csv(manifest_path, dtype=str).fillna("")
    required = {"effective_date", "anchor_csv", "adjustments_csv"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"anchor manifest missing columns: {sorted(missing)}")
    frame["effective_date"] = pd.to_datetime(frame["effective_date"], errors="raise").dt.normalize()
    if frame["effective_date"].duplicated().any():
        raise ValueError("anchor manifest contains duplicate effective dates")
    if not frame["effective_date"].is_monotonic_increasing:
        frame = frame.sort_values("effective_date").reset_index(drop=True)
    for column in ("anchor_csv", "adjustments_csv"):
        if (frame[column].astype(str).str.strip() == "").all() and column == "anchor_csv":
            raise ValueError("anchor manifest has no anchor files")
    return frame


def select_production_anchor(
    manifest_path: str | Path,
    target_date: str | pd.Timestamp,
    *,
    base_adjustments_path: str | Path,
) -> ProductionAnchor:
    """Select the latest known anchor already effective on target_date.

    Supplemental adjustment files accumulate from the manifest start through the
    selected anchor. A future announced anchor is deliberately not activated
    early, even if a live constituent endpoint has already switched snapshots.
    """
    manifest_path = Path(manifest_path)
    manifest = read_anchor_manifest(manifest_path)
    target = pd.Timestamp(target_date).normalize()
    eligible = manifest[manifest["effective_date"] <= target]
    if eligible.empty:
        raise ValueError(f"no production anchor is effective by {target.date()}")
    selected = eligible.iloc[-1]
    base_dir = manifest_path.parent
    anchor_path = (base_dir / str(selected["anchor_csv"])).resolve()
    adjustment_paths: list[Path] = [Path(base_adjustments_path).resolve()]
    for value in eligible["adjustments_csv"].astype(str):
        value = value.strip()
        if value:
            adjustment_paths.append((base_dir / value).resolve())
    return ProductionAnchor(
        effective_date=pd.Timestamp(selected["effective_date"]),
        anchor_path=anchor_path,
        adjustment_paths=tuple(adjustment_paths),
    )


def _combined_adjustments(paths: tuple[Path, ...]) -> pd.DataFrame:
    frames = [read_adjustments_csv(path) for path in paths]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["effective_date", "out_symbol"]).reset_index(drop=True)
    duplicates = combined.duplicated(["effective_date", "out_symbol", "in_symbol"])
    if duplicates.any():
        raise ValueError("duplicate adjustment rows across base/supplement files")
    return combined


def reconstruct_production_universe(
    *,
    manifest_path: str | Path,
    base_adjustments_path: str | Path,
    history_start: str | pd.Timestamp,
    history_end: str | pd.Timestamp,
    index_code: str = "000688",
    expected_constituents: int = 50,
) -> tuple[pd.DataFrame, pd.DataFrame, ProductionAnchor]:
    anchor_choice = select_production_anchor(
        manifest_path,
        history_end,
        base_adjustments_path=base_adjustments_path,
    )
    anchor = read_anchor_csv(anchor_choice.anchor_path)
    adjustments = _combined_adjustments(anchor_choice.adjustment_paths)
    latest_adjustment = pd.Timestamp(adjustments["effective_date"].max()).normalize()
    if latest_adjustment != anchor_choice.effective_date:
        raise ValueError(
            "selected anchor and cumulative adjustment history disagree: "
            f"anchor={anchor_choice.effective_date.date()} "
            f"latest_adjustment={latest_adjustment.date()}"
        )
    universe, segments, _ = reconstruct_index_history(
        anchor,
        adjustments,
        history_start=history_start,
        history_end=history_end,
        anchor_effective_date=anchor_choice.effective_date,
        expected_constituents=expected_constituents,
        index_code=index_code,
    )
    return universe, segments, anchor_choice


def active_symbols_on(universe: pd.DataFrame, target_date: str | pd.Timestamp) -> set[str]:
    target = pd.Timestamp(target_date).normalize()
    frame = universe.copy()
    frame["effective_start"] = pd.to_datetime(frame["effective_start"])
    frame["effective_end"] = pd.to_datetime(frame["effective_end"])
    active = frame[
        (frame["effective_start"] <= target)
        & (frame["effective_end"].isna() | (frame["effective_end"] >= target))
    ]
    return {normalize_symbol(v) for v in active["symbol"]}


def validate_live_snapshot(
    *,
    manifest_path: str | Path,
    target_date: str | pd.Timestamp,
    active_symbols: set[str],
    live_symbols: set[str],
    expected_constituents: int = 50,
) -> str:
    """Fail closed when the live snapshot is inconsistent with known anchors.

    Index providers can publish the next constituent snapshot before its trading
    effective date. Before a known future anchor, either the active set or that
    next known set is acceptable. Once the latest known anchor is effective,
    the live snapshot must match it exactly; a mismatch signals that reference
    data may be stale and production must stop until the new rebalance is added.
    """
    target = pd.Timestamp(target_date).normalize()
    active = {normalize_symbol(v) for v in active_symbols}
    live = {normalize_symbol(v) for v in live_symbols}
    if len(active) != expected_constituents:
        raise ValueError(f"active production universe has {len(active)} symbols")
    if len(live) != expected_constituents:
        raise ValueError(f"live index snapshot has {len(live)} symbols")
    if live == active:
        return "active"

    manifest_path = Path(manifest_path)
    manifest = read_anchor_manifest(manifest_path)
    future = manifest[manifest["effective_date"] > target]
    if not future.empty:
        next_row = future.iloc[0]
        next_anchor = read_anchor_csv(manifest_path.parent / str(next_row["anchor_csv"]))
        next_symbols = {normalize_symbol(v) for v in next_anchor["symbol"]}
        if live == next_symbols:
            return "next_known_anchor"

    raise ValueError(
        "live constituent snapshot does not match the active or next-known STAR50 anchor; "
        "production reference data may be stale"
    )

