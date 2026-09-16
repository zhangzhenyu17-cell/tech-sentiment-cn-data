from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.sector_limit_semantics import (
    enrich_baostock_structural_limit_rows,
    structural_limit_rule,
)
from tech_sentiment.sector_membership_contract import audit_931152_design_membership


ROOT = Path(__file__).resolve().parents[1]


def test_main_board_and_risk_warning_rules_are_date_aware() -> None:
    ordinary = structural_limit_rule(date="2023-06-12", board="SSE_MAIN", is_st=False)
    risk = structural_limit_rule(date="2023-06-12", board="SSE_MAIN", is_st=True)
    post_2026 = structural_limit_rule(date="2026-07-06", board="SSE_MAIN", is_st=True)

    assert ordinary.eligible and ordinary.limit_pct == 10.0
    assert risk.eligible and risk.limit_pct == 5.0
    assert post_2026.eligible and post_2026.limit_pct == 10.0


def test_chinext_reform_switches_ordinary_and_st_rules_together() -> None:
    pre_ordinary = structural_limit_rule(date="2020-08-21", board="CHINEXT", is_st=False)
    pre_st = structural_limit_rule(date="2020-08-21", board="CHINEXT", is_st=True)
    post_ordinary = structural_limit_rule(date="2020-08-24", board="CHINEXT", is_st=False)
    post_st = structural_limit_rule(date="2020-08-24", board="CHINEXT", is_st=True)

    assert pre_ordinary.limit_pct == 10.0
    assert pre_st.limit_pct == 5.0
    assert post_ordinary.limit_pct == 20.0
    assert post_st.limit_pct == 20.0


def test_star_structural_rule_is_20_but_predates_market_fail_closed() -> None:
    before = structural_limit_rule(date="2019-07-19", board="STAR", is_st=False)
    live = structural_limit_rule(date="2019-07-22", board="STAR", is_st=False)

    assert before.eligible is False and before.limit_pct is None
    assert live.eligible is True and live.limit_pct == 20.0


def test_baostock_status_does_not_bypass_special_day_evidence() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2023-06-12", "2023-06-12", "2023-06-12"],
            "code": ["sh.600001", "sz.300001", "sh.688001"],
            "board": ["SSE_MAIN", "CHINEXT", "STAR"],
            "tradestatus": ["1", "1", "0"],
            "isST": ["1", "0", "0"],
            "special_day_status": ["ordinary", "unknown", "ordinary"],
        }
    )

    out = enrich_baostock_structural_limit_rows(frame)

    assert out.loc[0, "limit_pct"] == 5.0
    assert bool(out.loc[0, "limit_eligible"]) is True
    assert out.loc[1, "limit_pct"] == 20.0
    assert bool(out.loc[1, "limit_eligible"]) is False
    assert out.loc[1, "limit_rule_reason"] == "special_day_status_not_evidenced"
    assert bool(out.loc[2, "limit_eligible"]) is False
    assert out.loc[2, "limit_rule_reason"] == "suspended_or_not_trading"


def test_explicit_no_limit_day_never_gets_extreme_feature_eligibility() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2023-04-10"],
            "code": ["sh.600001"],
            "board": ["SSE_MAIN"],
            "tradestatus": ["1"],
            "isST": ["0"],
            "special_day_status": ["no_limit"],
        }
    )
    out = enrich_baostock_structural_limit_rows(frame)
    assert out.loc[0, "limit_pct"] == 10.0
    assert bool(out.loc[0, "limit_eligible"]) is False
    assert out.loc[0, "limit_rule_reason"] == "explicit_no_limit_special_day"


def test_invalid_baostock_boolean_fails_closed() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2023-06-12"],
            "code": ["sh.600001"],
            "board": ["SSE_MAIN"],
            "tradestatus": ["maybe"],
            "isST": ["0"],
            "special_day_status": ["ordinary"],
        }
    )
    with pytest.raises(ValueError, match="tradestatus"):
        enrich_baostock_structural_limit_rows(frame)


def test_931152_design_membership_gate_remains_closed_until_2019_is_complete() -> None:
    manifest = pd.read_csv(ROOT / "data/reference/sector_931152_membership_evidence.csv")
    audit = audit_931152_design_membership(manifest)

    assert audit.eligible is False
    assert audit.expected_periods == 11
    assert audit.complete_periods == 8
    assert audit.pending_periods == ("2019-04", "2019-06", "2019-12")


def test_931152_calendar_and_partial_evidence_status_are_explicit() -> None:
    manifest = pd.read_csv(ROOT / "data/reference/sector_931152_membership_evidence.csv")
    expected_dates = {
        "2019-04": "2019-04-22",
        "2019-06": "2019-06-17",
        "2019-12": "2019-12-16",
        "2020-06": "2020-06-15",
        "2020-12": "2020-12-14",
        "2021-06": "2021-06-15",
        "2021-12": "2021-12-13",
        "2022-06": "2022-06-13",
        "2022-12": "2022-12-12",
        "2023-06": "2023-06-12",
        "2023-12": "2023-12-11",
    }
    assert dict(zip(manifest["expected_period"], manifest["effective_date"])) == expected_dates
    status_by_period = dict(zip(manifest["expected_period"], manifest["evidence_status"]))
    assert {period for period, status in status_by_period.items() if status == "pending"} == {
        "2019-04", "2019-06", "2019-12"
    }
    assert {period for period, status in status_by_period.items() if status == "complete"} == {
        "2020-06", "2020-12", "2021-06", "2021-12",
        "2022-06", "2022-12", "2023-06", "2023-12",
    }
    complete = manifest[manifest["evidence_status"].eq("complete")]
    assert complete["source_url"].notna().all()
    assert set(complete["change_status"]) == {"changed"}
