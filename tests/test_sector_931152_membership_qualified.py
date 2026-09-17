from pathlib import Path

import pandas as pd

from tech_sentiment.sector_membership_contract import audit_931152_design_membership
from tech_sentiment.universe import universe_diagnostics


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ROOT / "data/reference/sector_931152_design_universe.csv"
CHANGES = ROOT / "data/reference/sector_931152_official_changes_2019_2020.csv"
MANIFEST = ROOT / "data/reference/sector_931152_membership_evidence.csv"
SUMMARY = ROOT / "data/reference/sector_931152_2019_qualification_summary.csv"

PERIODS = {
    "2019-04": ("2019-04-22", 28),
    "2019-06": ("2019-06-17", 27),
    "2019-12": ("2019-12-16", 36),
    "2020-06": ("2020-06-15", 43),
    "2020-12": ("2020-12-14", 45),
    "2021-06": ("2021-06-15", 50),
    "2021-12": ("2021-12-13", 50),
    "2022-06": ("2022-06-13", 50),
    "2022-12": ("2022-12-12", 50),
    "2023-06": ("2023-06-12", 50),
    "2023-12": ("2023-12-11", 50),
}


def _universe() -> pd.DataFrame:
    frame = pd.read_csv(UNIVERSE, dtype={"symbol": str})
    frame["effective_start"] = pd.to_datetime(frame["effective_start"])
    frame["effective_end"] = pd.to_datetime(frame["effective_end"])
    return frame


def _members_on(frame: pd.DataFrame, value: str) -> set[str]:
    day = pd.Timestamp(value)
    mask = (frame["effective_start"] <= day) & (frame["effective_end"] >= day)
    return set(frame.loc[mask, "symbol"])


def test_qualified_design_universe_is_point_in_time_and_has_expected_shape() -> None:
    frame = _universe()
    diagnostics = universe_diagnostics(frame)
    assert diagnostics.has_point_in_time_history is True
    assert diagnostics.symbols == 71
    assert diagnostics.membership_rows == 81
    assert set(frame["source_index"].astype(str)) == {"931152"}
    assert set(frame["qualification_scope"]) == {"historical_design_research_input_only"}

    for _period, (effective_date, expected_size) in PERIODS.items():
        assert len(_members_on(frame, effective_date)) == expected_size


def test_2019_and_2020_official_adjustments_reconstruct_forward_exactly() -> None:
    frame = _universe()
    changes = pd.read_csv(CHANGES, dtype={"symbol": str})
    sequence = [
        ("2019-04-22", "2019-06-17"),
        ("2019-06-17", "2019-12-16"),
        ("2019-12-16", "2020-06-15"),
    ]
    expected_counts = {
        "2019-06-17": {"add": 0, "remove": 1},
        "2019-12-16": {"add": 12, "remove": 3},
        "2020-06-15": {"add": 7, "remove": 0},
    }

    for before_date, after_date in sequence:
        before = _members_on(frame, before_date)
        after = _members_on(frame, after_date)
        node = changes[changes["effective_date"].eq(after_date)]
        adds = set(node.loc[node["action"].eq("add"), "symbol"])
        removes = set(node.loc[node["action"].eq("remove"), "symbol"])
        assert {"add": len(adds), "remove": len(removes)} == expected_counts[after_date]
        assert (before - removes) | adds == after


def test_membership_changes_only_occur_on_registered_nodes() -> None:
    frame = _universe()
    effective_dates = {pd.Timestamp(date) for date, _size in PERIODS.values()}
    allowed_ends = {day - pd.Timedelta(days=1) for day in effective_dates if day != pd.Timestamp("2019-04-22")}
    allowed_ends.add(pd.Timestamp("2023-12-31"))
    assert set(frame["effective_start"]).issubset(effective_dates)
    assert set(frame["effective_end"]).issubset(allowed_ends)


def test_2019_crosscheck_summary_is_exact_and_holdout_closed() -> None:
    row = pd.read_csv(SUMMARY).iloc[0]
    assert int(row["launch_size"]) == 28
    assert int(row["2019_06_size"]) == 27
    assert int(row["2019_12_size"]) == 36
    assert int(row["2020_06_size"]) == 43
    assert int(row["sina_design_intervals_compared"]) == 78
    assert int(row["sina_interval_matches"]) == 78
    assert int(row["sina_interval_mismatches"]) == 0
    assert str(row["official_artifact_sha256"]) == "1077e5aa20f13da4f6d065760ada2fecf01f63873ed7074c60c5a8a14eaaecbf"
    assert str(row["secondary_artifact_sha256"]) == "97c8b6b24f3fd048f0f148c61c58678db66ef3efed8f9e7f73218cac2ffc2982"
    assert bool(row["holdout_opened"]) is False
    assert bool(row["model_outcomes_read"]) is False


def test_931152_membership_gate_is_fully_eligible() -> None:
    manifest = pd.read_csv(MANIFEST)
    audit = audit_931152_design_membership(manifest)
    assert audit.eligible is True
    assert audit.expected_periods == 11
    assert audit.complete_periods == 11
    assert audit.pending_periods == ()
    assert audit.errors == ()
