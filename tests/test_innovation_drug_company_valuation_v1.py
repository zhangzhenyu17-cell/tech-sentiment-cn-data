from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_company_valuation_v1 import (
    BUNDLE_VERSION,
    build_company_valuation_raw_v1,
)


def _facts() -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(period: str, eps: float, available: str, doc: str) -> None:
        rows.append(
            {
                "entity_id": "600276.SH",
                "period_end": period,
                "fact_type": "BASIC_EPS",
                "value": eps,
                "unit": "CNY_PER_SHARE",
                "evidence_available_date": available,
                "publication_timestamp": f"{available}T00:00:00",
                "document_id": doc,
                "revision_id": f"R-{doc}",
                "document_sha256": "a" * 64,
            }
        )

    add("2024-12-31", 1.00, "2025-03-31", "fy24")
    add("2024-06-30", 0.54, "2024-08-22", "h124")
    add("2025-06-30", 0.70, "2025-08-21", "h125")
    return pd.DataFrame(rows)


def _prices() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    dates = pd.bdate_range("2025-08-01", periods=45)
    prices = pd.DataFrame(
        {
            "date": dates,
            "symbol": "600276",
            "close": [50.0 + i * 0.1 for i in range(len(dates))],
            "provider": "TEST_PUBLIC_PRICE",
        }
    )
    return prices, dates


def test_exact_600276_raw_valuation_reuses_frozen_trailing_pe_semantics() -> None:
    prices, calendar = _prices()
    result = build_company_valuation_raw_v1(
        filing_facts=_facts(),
        stock_prices=prices,
        trading_dates=calendar,
        start_date=calendar[0],
        end_date=calendar[-1],
    )
    assert result.summary["bundle_version"] == BUNDLE_VERSION
    assert result.summary["symbol"] == "600276"
    assert result.summary["entity_id"] == "600276.SH"
    assert result.summary["usable_rows"] > 0
    assert result.summary["history_percentile_computed"] is False
    assert result.summary["valuation_state_classified"] is False
    assert result.summary["private_thresholds_used"] is False
    assert result.summary["forward_prices_or_returns_used"] is False
    assert result.summary["outcome_read"] is False
    assert result.summary["evidence_qualification_changed"] is False
    assert result.summary["production_changed"] is False
    assert result.summary["trading_authority_changed"] is False
    assert set(result.rail["entity_id"]) == {"600276.SH"}
    assert set(result.rail["price_provider"]) == {"TEST_PUBLIC_PRICE"}
    assert result.rail["valuation_change_20d"].notna().any()


def test_raw_valuation_rejects_non_600276_scope() -> None:
    prices, calendar = _prices()
    facts = _facts().assign(entity_id="600000.SH")
    with pytest.raises(ValueError, match="exact 600276.SH"):
        build_company_valuation_raw_v1(
            filing_facts=facts,
            stock_prices=prices,
            trading_dates=calendar,
            start_date=calendar[0],
            end_date=calendar[-1],
        )


def test_public_workflow_remains_manual_only_and_never_classifies_valuation() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / ".github/workflows/innovation-drug-company-fundamental-v1.yml"
    ).read_text()
    assert "workflow_dispatch:" in workflow
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert trigger not in workflow
    assert "materialize_innovation_drug_company_valuation_v1.py" in workflow

    source = (
        root / "src/tech_sentiment/innovation_drug_company_valuation_v1.py"
    ).read_text()
    assert '"history_percentile_computed": False' in source
    assert '"valuation_state_classified": False' in source
    assert '"private_thresholds_used": False' in source
