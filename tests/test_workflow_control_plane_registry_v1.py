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


def test_scheduled_public_workflows_serialize_runs() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for row in payload["workflows"]:
        if "schedule" not in row["triggers"]:
            continue
        text = _workflow_text(row["file"])
        assert "\nconcurrency:\n" in text, row["file"]
        assert "cancel-in-progress: false" in text, row["file"]


def test_public_ci_cancels_superseded_runs() -> None:
    text = _workflow_text("tests.yml")
    assert "\nconcurrency:\n" in text
    assert "cancel-in-progress: true" in text


def test_failure_capture_covers_all_public_automatic_and_active_prospective_leaf_rails() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    failure = _workflow_text("engineering-failure-capture.yml")

    for row in payload["workflows"]:
        if row["file"] == "engineering-failure-capture.yml":
            continue
        automatic = any(
            trigger in row["triggers"]
            for trigger in ("schedule", "workflow_run", "push", "pull_request")
        )
        if not automatic:
            continue
        match = re.search(
            r"^name:\s*[\"']?([^\"'\n]+)[\"']?",
            _workflow_text(row["file"]),
            flags=re.MULTILINE,
        )
        assert match is not None, row["file"]
        workflow_name = match.group(1).strip()
        assert f'- "{workflow_name}"' in failure, row["file"]

    assert '- "prospective-context-raw-preopen-v2"' in failure


def test_all_public_automatic_workflows_have_bounded_runtime() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for row in payload["workflows"]:
        if not any(
            trigger in row["triggers"]
            for trigger in ("schedule", "workflow_run", "push", "pull_request")
        ):
            continue
        text = _workflow_text(row["file"])
        assert "timeout-minutes:" in text, row["file"]


def test_public_automatic_write_surfaces_do_not_git_push_main() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for row in payload["workflows"]:
        if not any(
            trigger in row["triggers"]
            for trigger in ("schedule", "workflow_run", "push", "pull_request")
        ):
            continue
        text = _workflow_text(row["file"])
        assert "git push origin HEAD:main" not in text, row["file"]
        assert "git push origin main" not in text, row["file"]
