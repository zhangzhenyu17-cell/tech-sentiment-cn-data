import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v3_contract_is_shadow_only_and_never_retroactive() -> None:
    contract = json.loads(
        (ROOT / "reference/prospective_capture_timing_v3.json").read_text(encoding="utf-8")
    )
    assert contract["status"] == "FROZEN_SHADOW_ONLY"
    assert contract["activation_mode"] == "SHADOW_ONLY_NO_FORMAL_EVIDENCE_HANDOFF"
    assert contract["timing"]["capture_after_former_0530_allowed"] is True
    assert contract["timing"]["capture_after_decision_cutoff_allowed"] is True
    assert contract["eligibility"]["late_observation_never_retroactively_eligible"] is True
    assert contract["eligibility"]["formal_evidence_handoff"] is False
    assert contract["semantics"]["historical_backfill_allowed"] is False
    assert contract["semantics"]["forward_outcomes_allowed"] is False


def test_v3_workflow_collects_after_0530_without_formal_handoff() -> None:
    text = (
        ROOT / ".github/workflows/prospective-public-continuous-collector-v3.yml"
    ).read_text(encoding="utf-8")
    assert "prospective-public-continuous-collector-v3" in text
    assert 'cron: "15 1,2,4,6 * * *"' in text
    assert "time(14, 30)" in text
    assert "05:30" not in text
    assert "prospective-source-observation-v3-" in text
    assert "GITHUB_HOSTED" in text
    assert "formal evidence handoff" in text.lower()
    assert "historical backfill" in text.lower()
    assert "outcome read" in text.lower()
    assert "production change" in text.lower()
    assert "trading authority" in text.lower()
    assert "workflow_run:" not in text
    assert "pull_request:" not in text
    assert "push:" not in text


def test_v3_macos_fallback_is_public_only_and_shared_identity() -> None:
    text = (
        ROOT / ".github/workflows/prospective-public-continuous-collector-v3-macos-fallback.yml"
    ).read_text(encoding="utf-8")
    assert "runs-on: macos-15" in text
    assert 'cron: "30 0,10,20 * * *"' in text
    assert "group: prospective-public-continuous-collector-v3" in text
    assert "GITHUB_HOSTED_MACOS_FALLBACK" in text
    assert "prospective-source-observation-v3-" in text
    assert "formal evidence handoff" in text.lower()
    assert "historical backfill" in text.lower()
    assert "outcome read" in text.lower()
    assert "production change" in text.lower()
    assert "trading authority" in text.lower()
    assert "SELF_HOSTED" not in text
    assert "pull_request:" not in text
    assert "push:" not in text


def test_v3_contract_registers_transport_diversity_without_self_hosted() -> None:
    contract = json.loads(
        (ROOT / "reference/prospective_capture_timing_v3.json").read_text(encoding="utf-8")
    )
    transport = contract["transport"]
    assert transport["active_origins"] == [
        "GITHUB_HOSTED",
        "GITHUB_HOSTED_MACOS_FALLBACK",
    ]
    assert transport["same_official_source_identity_required"] is True
    assert transport["same_normalization_semantics_required"] is True
    assert transport["shared_immutable_release_identity"] is True
    assert transport["self_hosted_public_data_connected"] is False
    assert transport["self_hosted_requires_separate_security_audit"] is True
