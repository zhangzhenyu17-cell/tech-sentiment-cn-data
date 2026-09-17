import pandas as pd

from tech_sentiment.sector_design_input_contract import (
    DESIGN_END,
    DESIGN_START,
    STOCK_ADJUSTMENT,
    STOCK_WARMUP_START,
    audit_design_price_inputs,
)


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2018-01-02", "2019-04-22", "2023-12-29", "2018-01-02", "2023-12-29"],
            "symbol": ["600001", "600001", "600001", "300001", "300001"],
            "close": [10, 11, 12, 20, 21],
            "pct_chg": [0, 1, 1, 0, 1],
            "amount": [1, 1, 1, 1, 1],
            "provider": ["eastmoney"] * 5,
        }
    )


def _index() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2019-04-22", "2023-12-29"],
            "index_code": ["931152", "931152"],
            "close": [1000, 1200],
            "provider": ["akshare:index_zh_a_hist"] * 2,
        }
    )


def test_frozen_design_input_contract_is_qfq_and_never_crosses_2024() -> None:
    assert STOCK_ADJUSTMENT == "qfq"
    assert STOCK_WARMUP_START < DESIGN_START
    assert DESIGN_END == pd.Timestamp("2023-12-31")


def test_complete_public_design_inputs_pass() -> None:
    audit = audit_design_price_inputs(_prices(), _index(), ["600001", "300001"])
    assert audit.eligible_for_design_input is True
    assert audit.stock_symbols == 2
    assert audit.missing_symbols == ()


def test_holdout_row_fails_closed() -> None:
    prices = _prices().copy()
    prices.loc[len(prices)] = ["2024-01-02", "600001", 13, 1, 1, "eastmoney"]
    audit = audit_design_price_inputs(prices, _index(), ["600001", "300001"])
    assert audit.eligible_for_design_input is False
    assert "stock history crosses untouched holdout boundary" in audit.errors


def test_missing_symbol_fails_closed() -> None:
    prices = _prices()[lambda frame: frame["symbol"].eq("600001")].copy()
    audit = audit_design_price_inputs(prices, _index(), ["600001", "300001"])
    assert audit.eligible_for_design_input is False
    assert audit.missing_symbols == ("300001",)


def test_wrong_index_code_fails_closed() -> None:
    index = _index().copy()
    index["index_code"] = "000688"
    audit = audit_design_price_inputs(_prices(), index, ["600001", "300001"])
    assert audit.eligible_for_design_input is False
    assert any("unexpected index codes" in item for item in audit.errors)
