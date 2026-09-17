import pandas as pd

from tech_sentiment.sector_design_bundle_contract import (
    apply_qfq_design_price_semantics,
    attach_limit_qualification,
    audit_innovation_drug_design_bundle,
)


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600001", "300001"],
            "effective_start": ["2019-04-22", "2020-01-02"],
            "effective_end": ["2023-12-31", "2023-12-31"],
            "universe_mode": ["point_in_time", "point_in_time"],
        }
    )


def _manifest() -> dict[str, object]:
    return {
        "bundle_kind": "sector_design_public_input_v1",
        "index_code": "931152",
        "design_start": "2019-04-22",
        "design_end": "2023-12-31",
        "warmup_start": "2018-01-01",
        "technical_price_adjustment": "qfq",
        "pct_chg_semantics": "qfq_close_to_close",
        "membership_gate": "eligible",
        "limit_gate": "eligible",
        "minimum_daily_limit_coverage": 0.96,
        "qualification_scope": "historical_design_research_input_only",
        "holdout_included": False,
        "model_outcomes_included": False,
    }


def test_qfq_semantics_recomputes_pct_change_from_same_close_rail() -> None:
    raw = pd.DataFrame(
        {
            "date": ["2018-01-01", "2018-01-02", "2018-01-01", "2018-01-02"],
            "symbol": ["600001", "600001", "300001", "300001"],
            "close": [10.0, 11.0, 20.0, 18.0],
            "amount": [100.0, 110.0, 200.0, 180.0],
            "pct_chg": [999.0, 999.0, 999.0, 999.0],
        }
    )
    out = apply_qfq_design_price_semantics(raw)
    assert pd.isna(out.loc[(out["symbol"] == "600001") & (out["date"] == pd.Timestamp("2018-01-01")), "pct_chg"]).all()
    assert round(float(out.loc[(out["symbol"] == "600001") & (out["date"] == pd.Timestamp("2018-01-02")), "pct_chg"].iloc[0]), 6) == 10.0
    assert round(float(out.loc[(out["symbol"] == "300001") & (out["date"] == pd.Timestamp("2018-01-02")), "pct_chg"].iloc[0]), 6) == -10.0
    assert set(out["technical_price_adjustment"]) == {"qfq"}
    assert set(out["pct_chg_semantics"]) == {"qfq_close_to_close"}


def test_limit_attachment_requires_every_active_member_day_but_not_warmup() -> None:
    qfq = apply_qfq_design_price_semantics(
        pd.DataFrame(
            {
                "date": ["2018-01-01", "2019-04-22", "2019-04-22"],
                "symbol": ["600001", "600001", "300001"],
                "close": [5.0, 10.0, 8.0],
                "amount": [10.0, 20.0, 15.0],
            }
        )
    )
    limits = pd.DataFrame(
        {
            "date": ["2019-04-22"],
            "symbol": ["600001"],
            "limit_pct": [10.0],
            "limit_eligible": [True],
            "limit_rule_source": ["rule"],
        }
    )
    out = attach_limit_qualification(qfq, limits, _universe())
    warmup = out[out["date"].eq(pd.Timestamp("2018-01-01"))].iloc[0]
    assert bool(warmup["limit_eligible"]) is False
    assert warmup["limit_rule_source"] == "not_active_member_limit_not_required"


def test_limit_attachment_fails_when_active_member_day_is_missing() -> None:
    qfq = apply_qfq_design_price_semantics(
        pd.DataFrame(
            {
                "date": ["2019-04-22"],
                "symbol": ["600001"],
                "close": [10.0],
                "amount": [20.0],
            }
        )
    )
    import pytest
    with pytest.raises(ValueError, match="missing 1 active member-day limit rows"):
        attach_limit_qualification(qfq, pd.DataFrame(columns=["date", "symbol", "limit_pct", "limit_eligible", "limit_rule_source"]), _universe())


def test_bundle_audit_rejects_holdout_rows_even_if_manifest_claims_false() -> None:
    prices = apply_qfq_design_price_semantics(
        pd.DataFrame(
            {
                "date": ["2018-01-01", "2024-01-02"],
                "symbol": ["600001", "600001"],
                "close": [5.0, 10.0],
                "amount": [10.0, 20.0],
            }
        )
    )
    prices["limit_pct"] = [pd.NA, 10.0]
    prices["limit_eligible"] = [False, True]
    prices["limit_rule_source"] = ["warmup", "rule"]
    index_prices = pd.DataFrame(
        {"date": ["2019-04-22"], "index_code": ["931152"], "close": [1000.0]}
    )
    audit = audit_innovation_drug_design_bundle(prices, _universe(), index_prices, _manifest())
    assert audit.eligible is False
    assert "prices contain post-design/holdout rows" in audit.errors


def test_bundle_audit_accepts_design_only_qualified_shapes() -> None:
    prices = apply_qfq_design_price_semantics(
        pd.DataFrame(
            {
                "date": ["2018-01-01", "2019-04-22", "2023-12-29"],
                "symbol": ["600001", "600001", "600001"],
                "close": [5.0, 10.0, 12.0],
                "amount": [10.0, 20.0, 25.0],
            }
        )
    )
    prices["limit_pct"] = [pd.NA, 10.0, 10.0]
    prices["limit_eligible"] = [False, True, True]
    prices["limit_rule_source"] = ["warmup", "rule", "rule"]
    index_prices = pd.DataFrame(
        {
            "date": ["2019-04-22", "2023-12-29"],
            "index_code": ["931152", "931152"],
            "close": [1000.0, 1200.0],
        }
    )
    audit = audit_innovation_drug_design_bundle(prices, _universe(), index_prices, _manifest())
    assert audit.eligible is True
    assert audit.errors == ()
