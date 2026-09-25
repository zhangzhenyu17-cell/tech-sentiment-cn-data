from pathlib import Path

from tech_sentiment.innovation_drug_fundamental_scope_v1 import (
    SCOPE_VERSION,
    build_scope,
)

ROOT = Path(__file__).resolve().parents[1]


def test_company_alpha_scope_is_exact_and_outcome_blind() -> None:
    scope, manifest = build_scope(ROOT)
    assert scope["symbol"].tolist() == ["600276"]
    assert scope.iloc[0]["domain_id"] == "INNOVATION_DRUG"
    assert scope.iloc[0]["portfolio_role"] == "COMPANY_ALPHA"
    assert manifest["scope_version"] == SCOPE_VERSION
    assert manifest["design_membership_verified"] is True
    assert manifest["live_anchor_membership_verified"] is True
    assert manifest["outcome_read"] is False
    assert manifest["predictive_research_run"] is False
    assert manifest["evidence_qualification_changed"] is False
    assert manifest["production_changed"] is False
    assert manifest["trading_authority_changed"] is False
