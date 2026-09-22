from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REGISTRY = ROOT / "reference" / "workflow_control_plane_registry_v1.json"
TRIGGERS = ("workflow_dispatch", "schedule", "workflow_run", "push", "pull_request")


def _workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _triggers(text: str) -> list[str]:
    found = []
    for trigger in TRIGGERS:
        if re.search(rf"^\s{{0,2}}{re.escape(trigger)}:", text, flags=re.MULTILINE):
            found.append(trigger)
    return found


def _crons(text: str) -> list[str]:
    return sorted(
        value.strip()
        for value in re.findall(r"""cron:\s*["']?([^"'\n]+)["']?""", text)
    )


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


def test_every_public_workflow_is_registered_exactly_once() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    actual = sorted(path.name for path in WORKFLOWS.glob("*.yml"))
    registered = [row["file"] for row in payload["workflows"]]
    assert len(registered) == len(set(registered))
    assert sorted(registered) == actual


def test_registered_trigger_cron_and_permission_surface_matches_yaml() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for row in payload["workflows"]:
        text = _workflow_text(row["file"])
        assert row["triggers"] == _triggers(text), row["file"]
        assert sorted(row["crons"]) == _crons(text), row["file"]
        assert sorted(row["permissions"]) == _permissions(text), row["file"]


def test_automatic_surface_is_explicit_and_small() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    automated = sorted(
        row["file"]
        for row in payload["workflows"]
        if any(t in row["triggers"] for t in ("schedule", "workflow_run", "push", "pull_request"))
    )
    assert automated == sorted(payload["active_automated_allowlist"])


def test_legacy_prospective_v1_cannot_reactivate() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    row = next(x for x in payload["workflows"] if x["file"] == "prospective-context-raw-v1.yml")
    assert row["governance_class"] == "LEGACY_FAIL_CLOSED"
    text = _workflow_text(row["file"])
    assert "LEGACY_PROSPECTIVE_RAIL_SUPERSEDED_BY_TIMING_V2" in text
    assert "contents: write" not in text


def test_control_plane_registry_cannot_grant_model_or_trading_authority() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for key, value in payload["invariants"].items():
        assert value is False, key
    assert "MODEL_FORMULA" in payload["not_authoritative_for"]
    assert "EVIDENCE_QUALIFICATION" in payload["not_authoritative_for"]
    assert "TRADING_AUTHORITY" in payload["not_authoritative_for"]
