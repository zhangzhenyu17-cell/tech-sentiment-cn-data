import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v3_contract_is_formal_capture_and_never_retroactive() -> None:
    contract = json.loads(
        (ROOT / "reference/prospective_capture_timing_v3.json").read_text(encoding="utf-8")
    )
    assert contract["status"] == "ACTIVE_FORMAL_CAPTURE"
    assert contract["activation_mode"] == "FORMAL_CAPTURE_WITH_PRIVATE_QUALIFICATION"
    assert contract["timing"]["capture_after_former_0530_allowed"] is True
    assert contract["timing"]["capture_after_decision_cutoff_allowed"] is True
    assert contract["eligibility"]["late_observation_never_retroactively_eligible"] is True
    assert contract["eligibility"]["formal_evidence_handoff"] is False
    assert contract["automation"]["formal_consumer_dispatch_allowed"] is True
    assert contract["timing"]["formal_source_cutoff_asia_shanghai"] == "08:45"
    assert contract["timing"]["formal_artifact_deadline_asia_shanghai"] == "09:15"
    assert contract["semantics"]["historical_backfill_allowed"] is False
    assert contract["semantics"]["forward_outcomes_allowed"] is False


def test_v3_workflow_collects_after_0530_for_formal_capture_without_source_level_handoff() -> None:
    text = (
        ROOT / ".github/workflows/prospective-public-continuous-collector-v3.yml"
    ).read_text(encoding="utf-8")
    assert "prospective-public-continuous-collector-v3" in text
    assert 'cron: "15 1,2,4,6 * * *"' in text
    assert "time(14, 30)" in text
    assert "05:30" not in text
    assert "prospective-source-observation-v3-" in text
    assert "GITHUB_HOSTED" in text
    assert "no evidence handoff" in text.lower()
    assert "historical backfill" in text.lower()
    assert "outcome read" in text.lower()
    assert "production change" in text.lower()
    assert "trading authority" in text.lower()
    assert "workflow_run:" not in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "heal_prospective_source_observation_v3.py" in text
    assert "V3_PARTIAL_RELEASE_WITHOUT_ARCHIVE" in text
    assert "V3_SOURCE_RELEASE_INCOMPLETE_AFTER_HEAL" in text


def test_v3_macos_fallback_is_public_only_and_shared_identity() -> None:
    text = (
        ROOT / ".github/workflows/prospective-public-continuous-collector-v3-macos-fallback.yml"
    ).read_text(encoding="utf-8")
    assert "runs-on: macos-15-intel" in text
    assert 'python-version: "3.11.9"' in text
    assert 'cron: "30 0,10,20 * * *"' in text
    assert "group: prospective-public-continuous-collector-v3" in text
    assert "GITHUB_HOSTED_MACOS_FALLBACK" in text
    assert "prospective-source-observation-v3-" in text
    assert "no evidence handoff" in text.lower()
    assert "historical backfill" in text.lower()
    assert "outcome read" in text.lower()
    assert "production change" in text.lower()
    assert "trading authority" in text.lower()
    assert "SELF_HOSTED" not in text
    assert "[-5:]" not in text
    assert "reversed(closed)" not in text
    assert "V3_FALLBACK_MANUAL_PAIR_REQUIRES_BOTH_OR_NEITHER" in text
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
    automation = contract["automation"]
    assert automation["automatic_resolution_scope"] == "LATEST_CLOSED_SESSION_ONLY"
    assert automation["older_session_recovery_requires_explicit_exact_pair"] is True
