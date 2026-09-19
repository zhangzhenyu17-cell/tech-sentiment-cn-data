from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "v4a-issuer-aggregate.yml"
CONTRACT = ROOT / "reference" / "v4a_issuer_source_aggregate_compatibility_bridge_v1.json"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _contract() -> dict[str, object]:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _inline_python_blocks() -> list[str]:
    text = _workflow()
    step = text.split(
        "      - name: Download and exact-verify frozen issuer source bundles\n", 1
    )[1].split("      - name: Aggregate issuer sources\n", 1)[0]
    return [
        textwrap.dedent(match)
        for match in re.findall(
            r"          python - <<'PY'\n(?P<body>.*?)\n          PY\n",
            step,
            re.S,
        )
    ]


def _run_resolver(tmp_path: Path, *, start_date: str, end_date: str):
    blocks = _inline_python_blocks()
    assert len(blocks) == 2
    (tmp_path / "reference").mkdir(parents=True)
    shutil.copy2(
        CONTRACT,
        tmp_path
        / "reference"
        / "v4a_issuer_source_aggregate_compatibility_bridge_v1.json",
    )
    env = os.environ.copy()
    env.update({"START_DATE": start_date, "END_DATE": end_date})
    return subprocess.run(
        [sys.executable, "-c", blocks[0]],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_issuer_aggregate_bridge_contract_is_exact_and_boundary_preserving() -> None:
    payload = _contract()
    assert payload["schema_version"] == (
        "v4a-issuer-source-aggregate-compatibility-bridge-v1"
    )
    assert payload["status"] == "FROZEN_ONE_TIME_ENGINEERING_COMPATIBILITY"
    assert payload["qualification_window"] == {
        "start_date": "2022-01-04",
        "end_date": "2026-09-17",
    }
    assert payload["triggering_failure_run_id"] == 35453638482

    drift = payload["allowed_producer_drift"]
    assert drift["path"] == "scripts/assert_v4a_stage_qualifiable.py"
    assert (
        drift["source_sha256"]
        == "95fb8a1ed533fb906d1e09aba549c8541fad6caec65115e3f17e92dc5a4aea55"
    )
    assert (
        drift["current_sha256"]
        == "fb65819177c756e4dcf05d9d93704957a0a2fb83ee64f1a23bfadb1fd0cc8fbb"
    )
    assert drift["issuer_source_gate_semantics_changed"] is False
    assert drift["issuer_materialization_semantics_changed"] is False

    invariants = payload["invariants"]
    for key in (
        "exact_manifest_identity_required",
        "exact_archive_sha256_required",
        "exact_archive_bytes_required",
        "exact_stage_receipt_sha256_required",
        "exact_shared_input_identity_required",
        "current_producer_file_set_must_match_source_manifest",
        "all_non_allowlisted_producer_files_must_match_source_manifest_sha256",
        "allowlisted_drift_requires_exact_old_and_current_sha256",
    ):
        assert invariants[key] is True
    for key in (
        "provider_refetch_allowed",
        "source_bundle_republication_allowed",
        "new_qualification_granted",
        "formal_evidence_handoff",
        "evidence_source_eligibility_changed",
        "pit_no_lookahead_semantics_changed",
        "research_scope_changed",
        "future_outcomes_used",
        "model_thresholds_changed",
        "signal_definitions_changed",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        assert invariants[key] is False


def test_issuer_aggregate_bridge_freezes_all_three_published_source_identities() -> None:
    sources = _contract()["sources"]
    expected = {
        "issuer_cninfo": (
            35384463289,
            "29571066bbf69da5ba252982f71b592e07969314",
            "v4a-issuer_cninfo-20220104-20260917-fbac0195c08c71c7ad87",
            "717dd2b443b0e3def34f760335c172453a0d71e42e8783f074e9d51b679c515f",
            "10f4c7981f063f2a49c90c19fc36b39678eab1828289195f797c96be3d450a81",
        ),
        "issuer_sse": (
            35384501342,
            "29571066bbf69da5ba252982f71b592e07969314",
            "v4a-issuer_sse-20220104-20260917-a0517a42213d8f50baf9",
            "925d9894aebba600f6ee6f7b97ee365c8171e61067221c93b1677351177d6e6d",
            "8abd6156e6bcf4270fb097e03a4c801091fe01e7032d3550741311671c92e9b7",
        ),
        "issuer_szse": (
            35419134679,
            "396b7d4bc88022c62212ea30809718f25893f90e",
            "v4a-issuer_szse-20220104-20260917-9d60092b6b8709fe4096",
            "41387484fd4e2d83aa33d55a30a02bfea8dbb995dd05c8d03bd61a3eba840d10",
            "ae62c49ae7d9f710f147f61dd197a66a27187b8fa2a8ea43c3f1dcdbd3777eaa",
        ),
    }
    for family, values in expected.items():
        row = sources[family]
        assert (
            row["source_run_id"],
            row["source_commit"],
            row["asset_base"],
            row["bundle_identity"],
            row["archive_sha256"],
        ) == values
        assert row["run_attempt"] == 1


def test_current_allowlisted_qualifier_bytes_are_still_exact() -> None:
    drift = _contract()["allowed_producer_drift"]
    path = ROOT / drift["path"]
    data = path.read_bytes()
    assert len(data) == drift["current_bytes"]
    assert hashlib.sha256(data).hexdigest() == drift["current_sha256"]


def test_actual_workflow_bridge_resolver_executes_and_emits_old_asset_bases(
    tmp_path: Path,
) -> None:
    result = _run_resolver(
        tmp_path,
        start_date="2022-01-04",
        end_date="2026-09-17",
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(
        (tmp_path / "bundle-meta" / "issuer-compat-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan == {
        "issuer_cninfo": {
            "asset_base": "v4a-issuer_cninfo-20220104-20260917-fbac0195c08c71c7ad87"
        },
        "issuer_sse": {
            "asset_base": "v4a-issuer_sse-20220104-20260917-a0517a42213d8f50baf9"
        },
        "issuer_szse": {
            "asset_base": "v4a-issuer_szse-20220104-20260917-9d60092b6b8709fe4096"
        },
    }


def test_actual_workflow_bridge_resolver_fails_closed_outside_frozen_window(
    tmp_path: Path,
) -> None:
    result = _run_resolver(
        tmp_path,
        start_date="2022-01-05",
        end_date="2026-09-17",
    )
    assert result.returncode != 0
    assert "window mismatch" in result.stderr


def test_actual_workflow_exact_verifier_compiles_and_has_strict_drift_checks() -> None:
    blocks = _inline_python_blocks()
    assert len(blocks) == 2
    compile(blocks[1], "<issuer-aggregate-exact-verifier>", "exec")
    verifier = blocks[1]
    for phrase in (
        "set(source_rows) != set(current_rows)",
        "issuer aggregate non-allowlisted producer drift",
        "file_sha256(archive_path)",
        "verify_stage_receipt(",
        "_validate_family_stage_contract(target, family)",
        "provider_refetch",
    ):
        assert phrase in verifier


def test_issuer_aggregate_workflow_never_refetches_issuer_provider_history() -> None:
    text = _workflow()
    assert "Download and exact-verify frozen issuer source bundles" in text
    assert "issuer-compat-plan.json" in text
    assert "v4a_issuer_source_aggregate_compatibility_bridge_v1.json" in text
    for forbidden in (
        "materialize_pit_evidence.py",
        "materialize_v4a_szse_issuer.py",
        "check_issuer_archive_connectivity.py",
        "gh workflow run",
        "repository_dispatch",
    ):
        assert forbidden not in text
