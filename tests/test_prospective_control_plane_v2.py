from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REGISTRY = ROOT / "reference" / "prospective_control_plane_v2.json"
TRIGGERS = ("workflow_dispatch", "schedule", "workflow_run", "push", "pull_request")
AUTOMATIC = {"schedule", "workflow_run", "push", "pull_request"}


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _triggers(text: str) -> list[str]:
    return [
        trigger
        for trigger in TRIGGERS
        if re.search(rf"^  {re.escape(trigger)}:", text, flags=re.MULTILINE)
    ]


def _crons(text: str) -> list[str]:
    return sorted(re.findall(r'cron:\s*["\']([^"\']+)["\']', text))


def _permissions(text: str) -> list[str]:
    match = re.search(
        r"^permissions:\s*\n((?:\s{2,}.+\n?)*)",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        return []
    return sorted(
        line.strip()
        for line in match.group(1).splitlines()
        if line.strip()
    )


def _payload() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registered_public_prospective_surface_matches_yaml() -> None:
    payload = _payload()
    registered = {row["file"] for row in payload["workflows"]}
    expected = set(payload["active_chain"])
    expected.update(payload["legacy_fail_closed"])
    assert registered == expected

    for row in payload["workflows"]:
        text = _text(row["file"])
        assert row["triggers"] == _triggers(text), row["file"]
        assert sorted(row["crons"]) == _crons(text), row["file"]
        assert sorted(row["permissions"]) == _permissions(text), row["file"]


def test_formal_v3_public_automatic_surface_is_explicit() -> None:
    payload = _payload()
    automatic = [
        row["file"]
        for row in payload["workflows"]
        if AUTOMATIC.intersection(row["triggers"])
    ]
    assert automatic == [
        "prospective-public-continuous-collector-v3.yml",
        "prospective-public-continuous-collector-v3-macos-fallback.yml",
        "prospective-public-daily-orchestrator-v1.yml",
    ]
    orchestrator = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-public-daily-orchestrator-v1.yml"
    )
    assert sorted(orchestrator["crons"]) == sorted(
        [
            "45 15 * * *",
            "0,15,30,45 16-23 * * *",
            "0,15,30,45 0 * * *",
            "0 1 * * *",
        ]
    )
    primary = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-public-continuous-collector-v3.yml"
    )
    assert primary["lifecycle_class"] == "ACTIVE_FORMAL_PRIMARY_SOURCE_COLLECTOR"
    assert sorted(primary["crons"]) == sorted([
        "15,45 7-23 * * *",
        "15,45 0 * * *",
        "15 1,2,4,6 * * *",
    ])
    fallback = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-public-continuous-collector-v3-macos-fallback.yml"
    )
    assert fallback["lifecycle_class"] == "ACTIVE_FORMAL_NETWORK_FALLBACK_COLLECTOR"


def test_public_v1_capture_is_fail_closed_tombstone() -> None:
    payload = _payload()
    assert payload["legacy_fail_closed"] == ["prospective-context-raw-v1.yml"]
    row = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-context-raw-v1.yml"
    )
    assert row["lifecycle_class"] == "LEGACY_FAIL_CLOSED"
    text = _text(row["file"])
    assert "LEGACY_PROSPECTIVE_RAIL_SUPERSEDED_BY_TIMING_V2" in text
    assert "timeout-minutes: 2" in text
    assert "build_prospective_context_raw_v1.py" not in text
    assert "contents: write" not in text


def test_compatibility_raw_leaf_is_formal_v3_backed_and_manual_only() -> None:
    payload = _payload()
    row = next(
        item
        for item in payload["workflows"]
        if item["file"] == "prospective-context-raw-preopen-v2.yml"
    )
    assert row["lifecycle_class"] == "ACTIVE_FORMAL_V3_COMPATIBILITY_ASSEMBLER"
    assert row["triggers"] == ["workflow_dispatch"]
    text = _text(row["file"])
    assert "prospective-context-raw-preopen-v2" in text
    assert "formal V3-backed assembly" in text
    assert "08:45" in text
    assert "09:15" in text
    assert "--v3-package-root formal-v3-packages" in text
    for forbidden in ("\n  schedule:", "\n  workflow_run:", "\n  push:", "\n  pull_request:"):
        assert forbidden not in text


def test_control_plane_formal_v3_promotion_is_bounded() -> None:
    payload = _payload()
    timing = payload["timing_contract"]
    assert timing["formal_source_cutoff_asia_shanghai"] == "08:45"
    assert timing["formal_artifact_deadline_asia_shanghai"] == "09:15"
    assert timing["first_eligible_execution_asia_shanghai"] == "09:30"
    assert timing["continuous_capture_after_session_close"] is True
    assert timing["late_capture_continues_after_cutoff"] is True
    assert timing["late_capture_retroactive_qualification_allowed"] is False
    assert payload["shared_upstream_dependency"]["may_promote_prospective_evidence"] is False
    assert payload["invariants"]["formal_v3_replacement_authorized"] is True
    assert payload["invariants"]["evidence_qualification_change_allowed"] is True
    assert payload["invariants"]["active_capture_automatic_trigger_allowed"] is True
    assert payload["invariants"]["late_observation_retroactive_qualification_allowed"] is False
    for key in (
        "historical_backfill_allowed",
        "forward_outcome_read_allowed",
        "model_change_allowed",
        "threshold_change_allowed",
        "universe_change_allowed",
        "production_change_allowed",
        "trading_authority_change_allowed",
    ):
        assert payload["invariants"][key] is False, key
    assert "EVIDENCE_QUALIFICATION" in payload["not_authoritative_for"]
    assert "TRADING_AUTHORITY" in payload["not_authoritative_for"]
