import json

import numpy as np
import pandas as pd
import pytest

from tech_sentiment.trailing_valuation_pit import (
    VALUATION_SOURCE_ID,
    build_trailing_valuation_rail,
    valuation_rail_to_pit_evidence,
)


def _eps(period_end: str, available: str, value: float, doc: str) -> dict[str, object]:
    return {
        "entity_id": "600000.SH",
        "period_end": period_end,
        "fact_type": "BASIC_EPS",
        "value": value,
        "unit": "CNY_PER_SHARE",
        "evidence_available_date": available,
        "document_id": doc,
        "revision_id": f"DOCUMENT:{doc}",
        "document_sha256": (doc * 64)[:64],
    }


def test_ttm_denominator_uses_only_filing_versions_available_as_of_price_date():
    facts = pd.DataFrame(
        [
            _eps("2024-12-31", "2025-04-20", 1.00, "a"),
            _eps("2025-03-31", "2025-04-25", 0.30, "b"),
            _eps("2024-03-31", "2024-04-25", 0.20, "c"),
            # Future restatement of prior FY must not affect May prices.
            _eps("2024-12-31", "2025-07-01", 2.00, "r"),
        ]
    )
    prices = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-05-01", periods=25),
            "symbol": "600000",
            "close": np.linspace(11.0, 13.4, 25),
            "provider": "tencent",
        }
    )
    rail = build_trailing_valuation_rail(filing_facts=facts, stock_prices=prices)
    first = rail.iloc[0]
    assert first["ttm_eps"] == pytest.approx(1.10)
    trace = json.loads(first["formula_trace"])
    assert trace["prior_fy_document_id"] == "a"
    assert first["denominator_document_id"] == "b"


def test_valuation_change_is_exact_20_row_trading_horizon_and_pit_serializable():
    facts = pd.DataFrame(
        [
            _eps("2024-12-31", "2025-04-20", 1.00, "a"),
        ]
    )
    prices = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-05-01", periods=25),
            "symbol": "600000",
            "close": np.arange(10.0, 35.0),
            "provider": "tencent",
        }
    )
    rail = build_trailing_valuation_rail(filing_facts=facts, stock_prices=prices)
    assert pd.isna(rail.iloc[19]["valuation_change_20d"])
    assert rail.iloc[20]["valuation_change_20d"] == pytest.approx(30.0 / 10.0 - 1.0)
    evidence = valuation_rail_to_pit_evidence(rail)
    assert set(evidence["source_identity"]) == {VALUATION_SOURCE_ID}
    payload = json.loads(evidence.iloc[0]["evidence_payload"])
    assert payload["valuation_change_20d"] == pytest.approx(2.0)
    assert pd.Timestamp(evidence.iloc[0]["event_date"]) == pd.Timestamp(
        evidence.iloc[0]["evidence_available_date"]
    )
