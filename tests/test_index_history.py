from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.index_history import (
    read_adjustments_csv,
    read_anchor_csv,
    reconstruct_index_history,
)


ROOT = Path(__file__).resolve().parents[1]
ANCHOR = ROOT / "data/reference/kc50_anchor_2026-06-16.csv"
ADJUSTMENTS = ROOT / "data/reference/kc50_adjustments_2022_2026.csv"


def _reconstruct():
    return reconstruct_index_history(
        read_anchor_csv(ANCHOR),
        read_adjustments_csv(ADJUSTMENTS),
        history_start="2022-01-01",
        history_end="2026-09-11",
        anchor_effective_date="2026-06-15",
        expected_constituents=50,
        index_code="000688",
    )


def _members_on(segments: pd.DataFrame, date: str) -> set[str]:
    when = pd.Timestamp(date)
    active = segments[
        (segments["effective_start"] <= when) & (segments["effective_end"] >= when)
    ]
    return set(active["symbol"])


def test_reference_adjustments_have_expected_shape():
    adjustments = read_adjustments_csv(ADJUSTMENTS)
    assert len(adjustments) == 52
    assert adjustments["effective_date"].nunique() == 17
    assert adjustments["effective_date"].min() == pd.Timestamp("2022-03-14")
    assert adjustments["effective_date"].max() == pd.Timestamp("2026-06-15")


def test_reconstruction_keeps_exactly_50_constituents_every_segment():
    compact, segments, diagnostics = _reconstruct()

    counts = segments.groupby("segment_id")["symbol"].nunique()
    assert (counts == 50).all()
    assert diagnostics.adjustment_rows == 52
    assert diagnostics.adjustment_dates == 17
    assert diagnostics.segments == 18
    assert diagnostics.expected_constituents == 50
    assert diagnostics.min_segment_constituents == 50
    assert diagnostics.max_segment_constituents == 50
    assert diagnostics.variable_constituent_count is False
    assert set(compact["universe_mode"]) == {"point_in_time"}
    assert set(compact["source_index"]) == {"000688"}


def test_known_adjustments_flip_membership_at_effective_date():
    _, segments, _ = _reconstruct()

    before_june_2026 = _members_on(segments, "2026-06-14")
    after_june_2026 = _members_on(segments, "2026-06-15")
    assert "688114" in before_june_2026 and "688347" not in before_june_2026
    assert "688114" not in after_june_2026 and "688347" in after_june_2026

    before_sep_2024 = _members_on(segments, "2024-09-17")
    after_sep_2024 = _members_on(segments, "2024-09-18")
    assert "688390" in before_sep_2024 and "689009" not in before_sep_2024
    assert "688390" not in after_sep_2024 and "689009" in after_sep_2024

    before_dec_2023 = _members_on(segments, "2023-12-10")
    after_dec_2023 = _members_on(segments, "2023-12-11")
    assert "689009" in before_dec_2023 and "688506" not in before_dec_2023
    assert "689009" not in after_dec_2023 and "688506" in after_dec_2023


def test_reentries_remain_separate_intervals():
    compact, _, _ = _reconstruct()

    ninebot = compact[compact["symbol"] == "689009"].sort_values("effective_start")
    assert len(ninebot) == 2
    assert list(ninebot["effective_start"].dt.strftime("%Y-%m-%d")) == [
        "2022-01-01",
        "2024-09-18",
    ]
    assert ninebot.iloc[0]["effective_end"] == pd.Timestamp("2023-12-10")

    asr = compact[compact["symbol"] == "688220"].sort_values("effective_start")
    assert len(asr) == 2
    assert asr.iloc[0]["effective_start"] == pd.Timestamp("2022-09-13")
    assert asr.iloc[0]["effective_end"] == pd.Timestamp("2025-03-16")
    assert asr.iloc[1]["effective_start"] == pd.Timestamp("2025-12-15")


def test_bad_anchor_fails_loudly():
    anchor = read_anchor_csv(ANCHOR)
    anchor.loc[anchor["symbol"] == "688347", "symbol"] = "688114"

    with pytest.raises(ValueError, match="incoming symbols absent"):
        reconstruct_index_history(
            anchor,
            read_adjustments_csv(ADJUSTMENTS),
            history_start="2022-01-01",
            history_end="2026-09-11",
            anchor_effective_date="2026-06-15",
        )


def test_adjustment_reader_accepts_unmatched_additions_and_removals(tmp_path: Path):
    path = tmp_path / "variable_adjustments.csv"
    path.write_text(
        "effective_date,out_symbol,in_symbol\n"
        "2026-06-15,000003,000005\n"
        "2026-06-15,000004,\n"
        "2025-12-15,000006,000003\n"
        "2025-12-15,,000004\n",
        encoding="utf-8",
    )

    adjustments = read_adjustments_csv(path)
    june = adjustments[adjustments["effective_date"] == pd.Timestamp("2026-06-15")]
    december = adjustments[adjustments["effective_date"] == pd.Timestamp("2025-12-15")]

    assert set(june["out_symbol"].dropna()) == {"000003", "000004"}
    assert set(june["in_symbol"].dropna()) == {"000005"}
    assert set(december["out_symbol"].dropna()) == {"000006"}
    assert set(december["in_symbol"].dropna()) == {"000003", "000004"}


def test_adjustment_reader_rejects_row_with_both_sides_blank(tmp_path: Path):
    path = tmp_path / "bad_adjustments.csv"
    path.write_text(
        "effective_date,out_symbol,in_symbol\n"
        "2026-06-15,,\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="neither incoming nor outgoing"):
        read_adjustments_csv(path)


def test_variable_size_reconstruction_tracks_count_changes():
    anchor = pd.DataFrame({"symbol": ["000001", "000002", "000005"]})
    adjustments = pd.DataFrame(
        {
            "effective_date": [
                "2025-12-15",
                "2025-12-15",
                "2026-06-15",
                "2026-06-15",
            ],
            "out_symbol": ["000006", None, "000003", "000004"],
            "in_symbol": ["000003", "000004", "000005", None],
        }
    )

    compact, segments, diagnostics = reconstruct_index_history(
        anchor,
        adjustments,
        history_start="2025-01-01",
        history_end="2026-09-11",
        anchor_effective_date="2026-06-15",
        expected_constituents=None,
        index_code="H30590",
    )

    assert _members_on(segments, "2026-06-15") == {"000001", "000002", "000005"}
    assert _members_on(segments, "2026-06-14") == {
        "000001",
        "000002",
        "000003",
        "000004",
    }
    assert _members_on(segments, "2025-12-14") == {"000001", "000002", "000006"}

    counts = sorted(segments.groupby("segment_id")["symbol"].nunique().unique())
    assert counts == [3, 4]
    assert diagnostics.expected_constituents is None
    assert diagnostics.min_segment_constituents == 3
    assert diagnostics.max_segment_constituents == 4
    assert diagnostics.variable_constituent_count is True
    assert set(compact["source_index"]) == {"H30590"}


def test_variable_size_ledger_still_fails_in_fixed_count_mode():
    anchor = pd.DataFrame({"symbol": ["000001", "000002", "000005"]})
    adjustments = pd.DataFrame(
        {
            "effective_date": ["2026-06-15", "2026-06-15"],
            "out_symbol": ["000003", "000004"],
            "in_symbol": ["000005", None],
        }
    )

    with pytest.raises(ValueError, match="produced 4 constituents; expected 3"):
        reconstruct_index_history(
            anchor,
            adjustments,
            history_start="2026-01-01",
            history_end="2026-09-11",
            anchor_effective_date="2026-06-15",
            expected_constituents=3,
            index_code="TEST",
        )

