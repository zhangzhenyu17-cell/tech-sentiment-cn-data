import pandas as pd

from tech_sentiment.sector_limit_coverage_strict import (
    audit_strict_member_day_limit_coverage,
    build_expected_member_days,
)


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600001", "300001"],
            "effective_start": ["2023-01-02", "2023-01-03"],
            "effective_end": ["2023-01-04", "2023-01-04"],
            "universe_mode": ["point_in_time", "point_in_time"],
        }
    )


def test_expected_member_days_expand_independently_of_provider_rows() -> None:
    expected = build_expected_member_days(
        _universe(),
        ["2023-01-02", "2023-01-03", "2023-01-04"],
    )
    assert list(expected.groupby("date").size()) == [1, 2, 2]
    assert len(expected) == 5


def test_missing_provider_row_stays_in_denominator_and_fails_gate() -> None:
    rows = pd.DataFrame(
        {
            "date": ["2023-01-02", "2023-01-03", "2023-01-03", "2023-01-04"],
            "symbol": ["600001", "600001", "300001", "600001"],
            "limit_eligible": [True, True, True, True],
            "limit_rule_source": ["rule"] * 4,
        }
    )
    audit = audit_strict_member_day_limit_coverage(
        rows,
        _universe(),
        ["2023-01-02", "2023-01-03", "2023-01-04"],
        min_daily_coverage=0.95,
    )
    assert audit.eligible is False
    assert audit.expected_member_days == 5
    assert audit.observed_member_days == 4
    assert audit.missing_member_days == 1
    assert audit.minimum_daily_observation_coverage == 0.5
    assert audit.minimum_daily_coverage == 0.5
    assert audit.days_below_threshold == 1


def test_complete_expected_matrix_passes_when_all_member_days_are_eligible() -> None:
    rows = pd.DataFrame(
        {
            "date": [
                "2023-01-02",
                "2023-01-03", "2023-01-03",
                "2023-01-04", "2023-01-04",
            ],
            "symbol": ["600001", "600001", "300001", "600001", "300001"],
            "limit_eligible": [True] * 5,
            "limit_rule_source": ["rule"] * 5,
        }
    )
    audit = audit_strict_member_day_limit_coverage(
        rows,
        _universe(),
        ["2023-01-02", "2023-01-03", "2023-01-04"],
    )
    assert audit.eligible is True
    assert audit.expected_member_days == 5
    assert audit.observed_member_days == 5
    assert audit.eligible_member_days == 5
    assert audit.missing_member_days == 0
    assert audit.minimum_daily_coverage == 1.0
    assert audit.days_below_threshold == 0


def test_observed_but_ineligible_row_reduces_limit_coverage_not_observation_coverage() -> None:
    rows = pd.DataFrame(
        {
            "date": [
                "2023-01-02",
                "2023-01-03", "2023-01-03",
                "2023-01-04", "2023-01-04",
            ],
            "symbol": ["600001", "600001", "300001", "600001", "300001"],
            "limit_eligible": [True, True, True, True, False],
            "limit_rule_source": ["rule"] * 5,
        }
    )
    audit = audit_strict_member_day_limit_coverage(
        rows,
        _universe(),
        ["2023-01-02", "2023-01-03", "2023-01-04"],
    )
    assert audit.minimum_daily_observation_coverage == 1.0
    assert audit.minimum_daily_coverage == 0.5
    assert audit.eligible is False
