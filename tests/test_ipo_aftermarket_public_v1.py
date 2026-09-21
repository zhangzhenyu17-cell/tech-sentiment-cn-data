import pandas as pd

from tech_sentiment.ipo_aftermarket_public_v1 import (
    METADATA_COLUMNS,
    PRICE_COLUMNS,
    UNLOCK_COLUMNS,
    UNLOCK_STATUS_COLUMNS,
    allowlist_prices,
    infer_ipo_board,
    normalize_ipo_metadata,
    reject_private_columns,
    validate_public_bundle,
)


def test_board_mapping_is_explicit():
    assert infer_ipo_board("601091") == "SSE_MAIN"
    assert infer_ipo_board("001234") == "SZSE_MAIN"
    assert infer_ipo_board("688001") == "STAR"
    assert infer_ipo_board("301001") == "CHINEXT"
    assert infer_ipo_board("920001") == "BSE"


def test_metadata_discards_provider_first_day_outcomes():
    raw = pd.DataFrame(
        {
            "股票代码": ["601091"],
            "股票简称": ["示例"],
            "发行价": [4.39],
            "上市日期": ["2026-09-17"],
            "发行市盈率": [20.0],
            "行业市盈率": [30.0],
            "发行总数": ["10600"],
            "首日涨幅": [373.8],
            "打新收益": [8205.0],
            "首日收盘价": [20.8],
        }
    )
    out = normalize_ipo_metadata(raw, provider="fixture")
    assert list(out.columns) == METADATA_COLUMNS
    assert "首日涨幅" not in out.columns
    assert out.loc[0, "symbol"] == "601091"


def test_price_allowlist_drops_pct_change():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-17"],
            "symbol": ["601091"],
            "open": [13.0],
            "high": [21.0],
            "low": [12.0],
            "close": [20.8],
            "amount": [1.0],
            "turnover": [79.0],
            "board": ["main"],
            "provider": ["fixture"],
            "pct_chg": [373.8],
        }
    )
    out = allowlist_prices(frame)
    assert list(out.columns) == PRICE_COLUMNS
    assert "pct_chg" not in out.columns


def test_private_research_fields_fail_closed():
    try:
        reject_private_columns(pd.DataFrame({"symbol": ["601091"], "candidate_a": [True]}))
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("private research field must not enter public bundle")


def test_bundle_requires_unlock_status_for_every_symbol():
    metadata = pd.DataFrame(
        [["601091", "示例", "SSE_MAIN", "2026-09-17", 4.39, pd.NA, pd.NA, "", "fixture"]],
        columns=METADATA_COLUMNS,
    )
    prices = pd.DataFrame(
        [["2026-09-17", "601091", 13, 21, 12, 20.8, 1, 79, "main", "fixture"]],
        columns=PRICE_COLUMNS,
    )
    unlocks = pd.DataFrame(columns=UNLOCK_COLUMNS)
    statuses = pd.DataFrame(columns=UNLOCK_STATUS_COLUMNS)
    try:
        validate_public_bundle(metadata, prices, unlocks, statuses, minimum_price_symbol_coverage=0.95)
    except ValueError as exc:
        assert "unlock query status" in str(exc)
    else:
        raise AssertionError("missing unlock status must fail closed")
