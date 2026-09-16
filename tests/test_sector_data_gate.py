import pandas as pd

from tech_sentiment.sector_data_gate import (
    audit_daily_limit_rule_coverage,
    audit_membership_evidence,
)


def test_membership_evidence_fails_closed_on_pending_period():
    manifest = pd.DataFrame(
        {
            "expected_period": ["2020-06", "2020-12"],
            "effective_date": ["2020-06-15", ""],
            "change_status": ["changed", ""],
            "evidence_type": ["exchange_etf_pcf", ""],
            "source_url": ["https://example.test/pcf.xml", ""],
            "evidence_status": ["complete", "pending"],
        }
    )

    audit = audit_membership_evidence(
        manifest,
        expected_periods=["2020-06", "2020-12"],
    )

    assert audit.eligible is False
    assert audit.complete_periods == 1
    assert audit.pending_periods == ("2020-12",)
    assert audit.errors == ()


def test_membership_evidence_rejects_complete_row_without_primary_fields():
    manifest = pd.DataFrame(
        {
            "expected_period": ["2020-06"],
            "effective_date": [""],
            "change_status": ["unknown"],
            "evidence_type": [""],
            "source_url": [""],
            "evidence_status": ["complete"],
        }
    )

    audit = audit_membership_evidence(manifest, expected_periods=["2020-06"])

    assert audit.eligible is False
    assert any("change_status" in error for error in audit.errors)
    assert any("effective_date" in error for error in audit.errors)
    assert any("evidence_type" in error for error in audit.errors)
    assert any("source_url" in error for error in audit.errors)


def test_limit_rule_coverage_accepts_explicit_point_in_time_semantics():
    universe = pd.DataFrame(
        {
            "symbol": ["600001", "300001"],
            "effective_start": ["2020-01-01", "2020-01-01"],
            "effective_end": ["2020-01-02", "2020-01-02"],
            "universe_mode": ["point_in_time", "point_in_time"],
        }
    )
    prices = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-01", "2020-01-02", "2020-01-02"],
            "symbol": ["600001", "300001", "600001", "300001"],
            "close": [10.0, 20.0, 10.1, 20.2],
            "limit_pct": [10.0, 10.0, 10.0, 10.0],
            "limit_eligible": [True, True, True, True],
            "limit_rule_source": ["exchange_rule"] * 4,
        }
    )

    audit = audit_daily_limit_rule_coverage(
        prices,
        universe,
        min_daily_coverage=1.0,
    )

    assert audit.eligible is True
    assert audit.minimum_daily_coverage == 1.0
    assert audit.days_below_threshold == 0
    assert audit.invalid_rows == 0


def test_limit_rule_coverage_does_not_infer_missing_rule():
    universe = pd.DataFrame(
        {
            "symbol": ["600001", "300001"],
            "effective_start": ["2020-01-01", "2020-01-01"],
            "effective_end": ["2020-01-01", "2020-01-01"],
            "universe_mode": ["point_in_time", "point_in_time"],
        }
    )
    prices = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-01"],
            "symbol": ["600001", "300001"],
            "close": [10.0, 20.0],
            "limit_pct": [10.0, None],
            "limit_eligible": [True, False],
            "limit_rule_source": ["exchange_rule", "rule_unknown"],
        }
    )

    audit = audit_daily_limit_rule_coverage(
        prices,
        universe,
        min_daily_coverage=0.75,
    )

    assert audit.eligible is False
    assert audit.minimum_daily_coverage == 0.5
    assert audit.days_below_threshold == 1
    assert audit.invalid_rows == 0


def test_limit_rule_coverage_rejects_current_snapshot_universe():
    universe = pd.DataFrame(
        {
            "symbol": ["600001"],
            "effective_start": ["2020-01-01"],
            "effective_end": ["2020-01-01"],
            "universe_mode": ["current_snapshot"],
        }
    )
    prices = pd.DataFrame(
        {
            "date": ["2020-01-01"],
            "symbol": ["600001"],
            "close": [10.0],
            "limit_pct": [10.0],
            "limit_eligible": [True],
            "limit_rule_source": ["exchange_rule"],
        }
    )

    audit = audit_daily_limit_rule_coverage(
        prices,
        universe,
        min_daily_coverage=1.0,
    )

    assert audit.eligible is False
    assert "universe is not genuine point-in-time history" in audit.errors
