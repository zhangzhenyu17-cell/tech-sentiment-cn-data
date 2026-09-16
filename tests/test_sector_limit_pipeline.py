import pandas as pd

from tech_sentiment.sector_limit_pipeline import build_and_audit_sector_limit_rows


def test_limit_pipeline_passes_ordinary_main_board_rows() -> None:
    daily = pd.DataFrame(
        {
            "date": ["2022-01-04", "2022-01-05"],
            "code": ["sh.600123", "sh.600123"],
            "tradestatus": ["1", "1"],
            "isST": ["0", "0"],
        }
    )
    basic = pd.DataFrame({"code": ["sh.600123"], "ipoDate": ["2010-01-01"], "outDate": [""]})
    universe = pd.DataFrame(
        {
            "symbol": ["600123"],
            "effective_start": ["2022-01-01"],
            "effective_end": ["2022-01-31"],
            "universe_mode": ["point_in_time"],
        }
    )
    result = build_and_audit_sector_limit_rows(daily, basic, universe)
    assert result.coverage_audit.eligible is True
    assert result.coverage_audit.minimum_daily_coverage == 1.0
    assert result.rows["limit_pct"].tolist() == [10.0, 10.0]


def test_limit_pipeline_fails_coverage_when_listing_day_is_unknown() -> None:
    daily = pd.DataFrame(
        {
            "date": ["2022-01-04", "2022-01-05"],
            "code": ["sh.600123", "sh.600123"],
            "tradestatus": ["1", "1"],
            "isST": ["0", "0"],
        }
    )
    basic = pd.DataFrame({"code": ["sh.600123"], "ipoDate": ["2022-01-04"], "outDate": [""]})
    universe = pd.DataFrame(
        {
            "symbol": ["600123"],
            "effective_start": ["2022-01-01"],
            "effective_end": ["2022-01-31"],
            "universe_mode": ["point_in_time"],
        }
    )
    result = build_and_audit_sector_limit_rows(daily, basic, universe)
    assert result.coverage_audit.eligible is False
    assert result.coverage_audit.days_below_threshold == 1
    assert result.rows.loc[0, "limit_rule_reason"] == "special_day_status_not_evidenced"
