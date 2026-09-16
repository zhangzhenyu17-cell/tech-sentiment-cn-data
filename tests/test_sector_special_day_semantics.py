import pandas as pd
import pytest

from tech_sentiment.sector_special_day_semantics import (
    derive_special_day_status,
    infer_board_from_baostock_code,
)


def test_board_inference_for_supported_a_share_codes() -> None:
    assert infer_board_from_baostock_code("sh.688001") == "STAR"
    assert infer_board_from_baostock_code("sz.300001") == "CHINEXT"
    assert infer_board_from_baostock_code("sz.301001") == "CHINEXT"
    assert infer_board_from_baostock_code("sh.600000") == "SSE_MAIN"
    assert infer_board_from_baostock_code("sz.002001") == "SZSE_MAIN"
    assert infer_board_from_baostock_code("bj.430001") == "UNKNOWN"


def test_star_first_five_trading_sessions_are_no_limit() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-02", periods=7, freq="D"),
            "code": ["sh.688001"] * 7,
            "tradestatus": ["1"] * 7,
        }
    )
    basic = pd.DataFrame({"code": ["sh.688001"], "ipoDate": ["2020-01-02"], "outDate": [""]})
    out, audit = derive_special_day_status(daily, basic)
    assert out["special_day_status"].tolist()[:5] == ["no_limit"] * 5
    assert out["special_day_status"].tolist()[5:] == ["ordinary", "ordinary"]
    assert audit.no_limit_rows == 5


def test_chinext_post_reform_uses_first_five_trading_rows_not_calendar_days() -> None:
    daily = pd.DataFrame(
        {
            "date": ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08", "2021-01-11"],
            "code": ["sz.300999"] * 6,
            "tradestatus": ["1", "0", "1", "1", "1", "1"],
        }
    )
    basic = pd.DataFrame({"code": ["sz.300999"], "ipoDate": ["2021-01-04"], "outDate": [""]})
    out, audit = derive_special_day_status(daily, basic)
    # Suspended 2021-01-05 does not consume one of the five trading-session slots.
    assert out.loc[out["date"].eq(pd.Timestamp("2021-01-11")), "special_day_status"].item() == "no_limit"
    assert audit.no_limit_rows == 5


def test_main_board_listing_day_fails_closed_then_becomes_ordinary() -> None:
    daily = pd.DataFrame(
        {
            "date": ["2020-06-01", "2020-06-02"],
            "code": ["sh.600123", "sh.600123"],
            "tradestatus": ["1", "1"],
        }
    )
    basic = pd.DataFrame({"code": ["sh.600123"], "ipoDate": ["2020-06-01"], "outDate": [""]})
    out, audit = derive_special_day_status(daily, basic)
    assert out["special_day_status"].tolist() == ["unknown", "ordinary"]
    assert audit.unknown_rows == 1


def test_explicit_override_takes_precedence_and_requires_source() -> None:
    daily = pd.DataFrame({"date": ["2022-03-01"], "code": ["sh.600123"], "tradestatus": ["1"]})
    basic = pd.DataFrame({"code": ["sh.600123"], "ipoDate": ["2010-01-01"], "outDate": [""]})
    overrides = pd.DataFrame(
        {
            "date": ["2022-03-01"],
            "code": ["sh.600123"],
            "special_day_status": ["no_limit"],
            "special_day_source": ["https://example.test/exchange-notice"],
        }
    )
    out, _ = derive_special_day_status(daily, basic, overrides=overrides)
    assert out.loc[0, "special_day_status"] == "no_limit"
    assert out.loc[0, "special_day_reason"] == "explicit_override"

    bad = overrides.copy()
    bad["special_day_source"] = ""
    with pytest.raises(ValueError, match="non-empty special_day_source"):
        derive_special_day_status(daily, basic, overrides=bad)


def test_missing_lifecycle_row_fails_closed() -> None:
    daily = pd.DataFrame({"date": ["2022-03-01"], "code": ["sh.600123"], "tradestatus": ["1"]})
    basic = pd.DataFrame({"code": ["sh.600999"], "ipoDate": ["2010-01-01"], "outDate": [""]})
    with pytest.raises(ValueError, match="missing stock_basic lifecycle rows"):
        derive_special_day_status(daily, basic)
