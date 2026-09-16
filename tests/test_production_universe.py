from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.production_universe import (
    active_symbols_on,
    reconstruct_production_universe,
    select_production_anchor,
    validate_live_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
MANIFEST = REF / "kc50_anchor_manifest.csv"
BASE_ADJUSTMENTS = REF / "kc50_adjustments_2022_2026.csv"


def test_weekend_before_q3_rebalance_uses_june_anchor():
    selected = select_production_anchor(
        MANIFEST,
        "2026-09-12",
        base_adjustments_path=BASE_ADJUSTMENTS,
    )
    assert selected.effective_date == pd.Timestamp("2026-06-15")
    assert selected.anchor_path.name == "kc50_anchor_2026-06-16.csv"
    assert len(selected.adjustment_paths) == 1


def test_q3_effective_date_activates_new_anchor_and_supplement():
    selected = select_production_anchor(
        MANIFEST,
        "2026-09-14",
        base_adjustments_path=BASE_ADJUSTMENTS,
    )
    assert selected.effective_date == pd.Timestamp("2026-09-14")
    assert selected.anchor_path.name == "kc50_anchor_2026-09-14.csv"
    assert [p.name for p in selected.adjustment_paths] == [
        "kc50_adjustments_2022_2026.csv",
        "kc50_adjustments_2026_q3.csv",
    ]


def test_reconstructed_membership_switches_only_on_effective_date():
    universe, _, selected = reconstruct_production_universe(
        manifest_path=MANIFEST,
        base_adjustments_path=BASE_ADJUSTMENTS,
        history_start="2026-06-01",
        history_end="2026-09-14",
    )
    assert selected.effective_date == pd.Timestamp("2026-09-14")

    before = active_symbols_on(universe, "2026-09-11")
    after = active_symbols_on(universe, "2026-09-14")
    assert len(before) == len(after) == 50

    outgoing = {"688065", "688297", "688538", "688608", "688617"}
    incoming = {"688002", "688629", "688729", "688775", "688820"}
    assert outgoing <= before
    assert incoming.isdisjoint(before)
    assert incoming <= after
    assert outgoing.isdisjoint(after)


def test_pre_effective_live_snapshot_may_equal_next_known_anchor():
    universe, _, _ = reconstruct_production_universe(
        manifest_path=MANIFEST,
        base_adjustments_path=BASE_ADJUSTMENTS,
        history_start="2026-06-01",
        history_end="2026-09-12",
    )
    active = active_symbols_on(universe, "2026-09-12")
    next_anchor = pd.read_csv(REF / "kc50_anchor_2026-09-14.csv", dtype={"symbol": str})
    relation = validate_live_snapshot(
        manifest_path=MANIFEST,
        target_date="2026-09-12",
        active_symbols=active,
        live_symbols=set(next_anchor["symbol"]),
    )
    assert relation == "next_known_anchor"


def test_unknown_live_snapshot_change_fails_closed_after_latest_anchor():
    universe, _, _ = reconstruct_production_universe(
        manifest_path=MANIFEST,
        base_adjustments_path=BASE_ADJUSTMENTS,
        history_start="2026-06-01",
        history_end="2026-09-14",
    )
    active = active_symbols_on(universe, "2026-09-14")
    bad_live = set(active)
    bad_live.remove(next(iter(bad_live)))
    bad_live.add("688999")
    with pytest.raises(ValueError, match="reference data may be stale"):
        validate_live_snapshot(
            manifest_path=MANIFEST,
            target_date="2026-09-14",
            active_symbols=active,
            live_symbols=bad_live,
        )

