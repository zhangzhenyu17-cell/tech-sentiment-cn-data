from pathlib import Path
import json
import os
import re
import subprocess
import sys
import textwrap


WORKFLOW_DIR = Path(".github/workflows")
FINAL = WORKFLOW_DIR / "qualify-capital-inputs.yml"
SZSE_MIGRATION = WORKFLOW_DIR / "v4a-issuer-szse-migration.yml"

STAGE_WORKFLOWS = (
    WORKFLOW_DIR / "v4a-shared-inputs.yml",
    WORKFLOW_DIR / "v4a-capital.yml",
    WORKFLOW_DIR / "v4a-financing.yml",
    WORKFLOW_DIR / "v4a-issuer-source.yml",
    SZSE_MIGRATION,
    WORKFLOW_DIR / "v4a-issuer-aggregate.yml",
    WORKFLOW_DIR / "v4a-fundamental-earnings.yml",
    WORKFLOW_DIR / "v4a-prices.yml",
    WORKFLOW_DIR / "v4a-policy.yml",
    WORKFLOW_DIR / "v4a-derived.yml",
)
ALL_V4A_WORKFLOWS = STAGE_WORKFLOWS + (FINAL,)


def _text(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def _fundamental_reuse_resolver_python() -> str:
    text = _text(WORKFLOW_DIR / "v4a-fundamental-earnings.yml")
    match = re.search(
        r"      - name: Resolve frozen filing checkpoint reuse\n"
        r".*?        run: \|\n"
        r"          python - <<'PY'\n"
        r"(?P<body>.*?)\n"
        r"          PY\n",
        text,
        re.S,
    )
    assert match is not None
    return textwrap.dedent(match.group("body"))


def _run_fundamental_reuse_resolver(tmp_path: Path, *, current_ref: str):
    env = os.environ.copy()
    env.update(
        {
            "START_DATE": "2022-01-04",
            "END_DATE": "2026-09-17",
            "CURRENT_SHA": "b4e4e12599ee39a5ec67847d0f2530fe85488b38",
            "CURRENT_REF": current_ref,
            "SHARD": "0",
            "GITHUB_ENV": str(tmp_path / "github-env"),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", _fundamental_reuse_resolver_python()],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fundamental_reuse_resolver_accepts_exact_recovery_cache_branch(tmp_path):
    result = _run_fundamental_reuse_resolver(
        tmp_path,
        current_ref="refs/heads/v4a/fundamental-resume-295710",
    )
    assert result.returncode == 0, result.stderr
    github_env = (tmp_path / "github-env").read_text(encoding="utf-8")
    assert "PRESENTATION_CURRENT_PROGRESS_CACHE_KEY=" in github_env
    assert "PRESENTATION_INTERMEDIATE_PROGRESS_CACHE_KEY=" in github_env
    assert "PRESENTATION_PRIOR_PROGRESS_CACHE_KEY=" in github_env


def test_fundamental_reuse_resolver_rejects_default_branch_cache_scope(tmp_path):
    result = _run_fundamental_reuse_resolver(
        tmp_path,
        current_ref="refs/heads/main",
    )
    assert result.returncode != 0
    assert "presentation progress caches are branch-scoped" in result.stderr


def test_all_v4a_workflows_are_manual_only():
    for path in ALL_V4A_WORKFLOWS:
        text = _text(path)
        assert re.search(r"(?m)^on:\s*$", text)
        assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
        for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
            assert forbidden not in text, (path, forbidden)


def test_final_workflow_identity_is_preserved_for_private_intake():
    text = _text(FINAL)
    assert text.startswith("name: qualify-capital-inputs\n")
    assert "capital-pit-input-materialization" in text
    assert "write_capital_pit_artifact_receipt.py" in text
    assert "finalize_capital_pit_materialization.py" in text
    assert "build_v4a_stage_lineage_summary.py" in text


def test_final_workflow_is_assembly_only_and_never_refetches_public_data():
    text = _text(FINAL)
    for forbidden in (
        "qualify_capital_inputs.py",
        "materialize_financing_history.py",
        "materialize_pit_evidence.py",
        "materialize_v4a_fundamental_earnings_shard.py",
        "materialize_v4a_price_shard.py",
        "materialize_v4a_policy_stage.py",
        "assemble_v4a_derived_pit.py",
        "check_cninfo_connectivity.py",
        "check_csrc_policy_connectivity.py",
        "check_issuer_archive_connectivity.py",
        "check_v4a_source_freshness.py",
    ):
        assert forbidden not in text, forbidden
    assert text.count("v4a_persistent_stage_bundle.py verify") >= 5
    assert "Download and exact-verify frozen issuer-source bundles" in text
    assert "v4a-stage-bundles-v1" in text


def test_every_stage_producer_publishes_an_immutable_reusable_bundle():
    for path in STAGE_WORKFLOWS:
        text = _text(path)
        assert "v4a_persistent_stage_bundle.py" in text
        assert "publish_v4a_release_bundle.sh" in text
        assert "v4a-stage-bundles-v1" in text
        assert "retention-days: 90" in text
        assert "workflow_dispatch:" in text


def test_no_stage_workflow_automatically_triggers_another_workflow():
    for path in ALL_V4A_WORKFLOWS:
        text = _text(path)
        assert "workflow_run:" not in text
        assert "repository_dispatch:" not in text
        assert "gh workflow run" not in text
        assert "actions/github-script" not in text


def test_issuer_sources_are_independently_manual_and_cninfo_keeps_bounded_shards():
    text = _text(WORKFLOW_DIR / "v4a-issuer-source.yml")
    assert "type: choice" in text
    for source in ("cninfo", "sse", "szse"):
        assert f"          - {source}" in text
        assert f"inputs.source == '{source}'" in text
    assert "max-parallel: 4" in text
    assert "shard: [0, 1, 2, 3]" in text
    assert "--source CNINFO_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SZSE_ANNOUNCEMENT_ARCHIVE" in text


def test_stage_cache_namespaces_remain_exact_commit_and_stage_compatible():
    cache_workflows = (
        "v4a-capital.yml",
        "v4a-financing.yml",
        "v4a-issuer-source.yml",
        "v4a-issuer-szse-migration.yml",
        "v4a-prices.yml",
        "v4a-policy.yml",
    )
    for name in cache_workflows:
        text = _text(WORKFLOW_DIR / name)
        assert "STAGE_COMPAT" in text
        assert "actions/cache/restore@v4" in text
        assert "actions/cache/save@v4" in text
        cache_lines = "\n".join(
            line for line in text.splitlines()
            if "key:" in line or "restore-keys:" in line
        )
        assert "STAGE_COMPAT" in cache_lines
        assert "github.sha" in cache_lines


def test_fundamental_uses_bounded_durable_progress_units_without_evidence_handoff():
    text = _text(WORKFLOW_DIR / "v4a-fundamental-earnings.yml")
    assert "max-parallel: 4" in text
    assert (
        "shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]"
        in text
    )
    assert "FUNDAMENTAL_WORK_UNIT_COUNT" in text
    assert "SPLIT_LEGACY_AND_CURRENT_PROGRESS_STORES_V1" in text
    assert "Restore durable V9 progress" in text
    assert "Restore presentation-fix-compatible current-run V9 progress" in text
    assert "Restore presentation-fix-compatible intermediate V9 progress" in text
    assert "Restore presentation-fix-compatible prior V9 progress" in text
    assert "PRESENTATION_CURRENT_PROGRESS_CACHE_KEY" in text
    assert "PRESENTATION_INTERMEDIATE_PROGRESS_CACHE_KEY" in text
    assert "PRESENTATION_PRIOR_PROGRESS_CACHE_KEY" in text
    assert "35444225741" in text
    assert "35440581921" in text
    assert "35438201372" in text
    assert "8caa5609ace700126e47e439c596e368e5a1e8d456881061b2167b62167581b5" in text
    assert "e77232e9d11a5739a3010794d6cd6c7c58046b1dc8bd76fbae46a4d3309808f3" in text
    assert "steps.durable_progress_restore.outputs.cache-hit == ''" in text
    assert "Restore cancelled-run mixed V8/V9 cache for legacy queries" in text
    assert (
        "if: ${{ steps.work_unit_restore.outputs.reused != 'true' }}\n"
        "        id: cancelled_bridge_restore"
        in text
    )
    assert "Migrate validated cancelled-run V9 checkpoints into durable progress" in text
    assert "Restore legacy V8 fallback when cancelled-run cache is unavailable" in text
    assert "Isolate restored mixed or legacy checkpoints read-only" in text
    assert "migrate_v4a_cancelled_fundamental_cache.py" in text
    assert "CANCELLED_RUN_CACHE_KEY" in text
    assert "CANCELLED_RUN_SOURCE_COMMIT" in text
    assert "Save durable V9 work-unit progress" in text
    assert ".cache/capital_pit_v4a/derived/progress" in text
    assert ".cache/capital_pit_v4a/derived/legacy" in text
    assert "--legacy-checkpoint-dir" in text
    assert "--progress-checkpoint-source-commit" in text
    assert "FUNDAMENTAL_WORK_UNIT_COUNT" in text
    assert "partial progress cache cannot be formal evidence" in text
    assert "v4a_fundamental_progress_bundle.py key" in text
    assert "v4a_fundamental_progress_bundle.py verify" in text
    assert "v4a_fundamental_progress_bundle.py rebase" in text
    assert "v4a_fundamental_progress_bundle.py package" in text
    assert "Restore immutable completed work unit" in text
    assert "Publish immutable completed work unit" in text
    assert "v4a-stage-bundles-v1" in text
    assert "persistent work-unit must require full group assembly" in text
    assert "old immutable work units cannot bridge presentation semantics" in text
    assert "presentation conflict reproof invariant missing" in text
    assert "CURRENT_REF: ${{ github.ref }}" in text
    assert "presentation progress caches are branch-scoped" in text
    assert "v4a/fundamental-resume-295710" in text

    contract = json.loads(
        Path("reference/v4a_fundamental_checkpoint_reuse_contract_v1.json").read_text(
            encoding="utf-8"
        )
    )
    bridge = contract["presentation_classifier_progress_bridge"]
    assert bridge["cache_scope_branch"] == "v4a/fundamental-resume-295710"
    assert bridge["cache_scope_ref"] == "refs/heads/v4a/fundamental-resume-295710"
    assert bridge["require_same_branch_recovery_until_v2_immutable_complete"] is True
    assert bridge["default_branch_direct_restore_assumed"] is False
    assert bridge["workflow_only_branch_guard_removal_allowed_after_v2_immutable_complete"] is True
    assert [
        (
            item["run_id"],
            item["run_attempt"],
            item["source_commit"],
            item["source_semantic_fingerprint"],
            item["source_semantic_key"],
        )
        for item in bridge["exact_source_runs"]
    ] == [
        (
            35444225741,
            1,
            "b905c91c3ca46d8d3d2758d8f11f968d72552d8f",
            "8caa5609ace700126e47e439c596e368e5a1e8d456881061b2167b62167581b5",
            "8caa5609ace700126e47",
        ),
        (
            35440581921,
            1,
            "d2385d1cdfe0561c7bd3a9515468ba1d37e6261c",
            "8caa5609ace700126e47e439c596e368e5a1e8d456881061b2167b62167581b5",
            "8caa5609ace700126e47",
        ),
        (
            35438201372,
            1,
            "69e515090bbb0728b8771aa1581a5909eb9d3613",
            "e77232e9d11a5739a3010794d6cd6c7c58046b1dc8bd76fbae46a4d3309808f3",
            "e77232e9d11a5739a301",
        ),
    ]
    assert bridge["exact_source_runs"][1]["verified_saved_units"] == list(range(8))
    assert bridge["exact_source_runs"][1]["completed_materialization_units"] == [0, 1, 2, 3]
    assert bridge["exact_source_runs"][1]["partial_cancelled_materialization_units"] == [4, 5, 6, 7]

    progress_cache_lines = "\n".join(
        line for line in text.splitlines() if "v4a-fund-progress-" in line
    )
    assert "PROGRESS_CACHE_ID" in progress_cache_lines
    assert "PROGRESS_SEMANTIC_KEY" in progress_cache_lines
    assert "inputs.start_date" in progress_cache_lines
    assert "inputs.end_date" in progress_cache_lines
    assert "matrix.shard" in progress_cache_lines
    assert "github.sha" not in progress_cache_lines

def test_derived_workflow_consumes_only_verified_persistent_upstreams():
    text = _text(WORKFLOW_DIR / "v4a-derived.yml")
    for family in ("shared", "issuer_aggregate", "fundamental", "prices", "policy"):
        assert family in text
    assert "v4a_persistent_stage_bundle.py verify" in text
    assert "assemble_v4a_derived_pit.py" in text
    assert "--issuer-source-commit" in text
    assert "--fundamental-source-commit" in text
    assert "--price-source-commit" in text
    assert "--policy-source-commit" in text


def test_issuer_aggregate_tracks_original_source_commits():
    text = _text(WORKFLOW_DIR / "v4a-issuer-aggregate.yml")
    assert "--cninfo-source-commit" in text
    assert "--sse-source-commit" in text
    assert "--szse-source-commit" in text
    assert "--input-manifest cninfo=bundle-meta/issuer_cninfo.json" in text
    assert "--input-manifest sse=bundle-meta/issuer_sse.json" in text
    assert "--input-manifest szse=bundle-meta/issuer_szse.json" in text


def test_stage_diagnostics_are_preserved_before_qualification_failure():
    required = {
        "v4a-capital.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-financing.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-issuer-source.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-issuer-szse-migration.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-fundamental-earnings.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-policy.yml": "assert_v4a_stage_qualifiable.py",
        "v4a-derived.yml": "assert_v4a_stage_qualifiable.py",
    }
    for name, gate in required.items():
        text = _text(WORKFLOW_DIR / name)
        first_upload = text.index("actions/upload-artifact@v4")
        first_gate = text.index(f"python scripts/{gate}")
        assert first_upload < first_gate, name


def test_manual_pipeline_has_explicit_failure_isolation_order():
    expected_names = (
        "v4a-01-shared-inputs",
        "v4a-02-capital",
        "v4a-03-financing",
        "v4a-04-issuer-source",
        "v4a-04b-issuer-szse-migration",
        "v4a-05-issuer-aggregate",
        "v4a-06-fundamental-earnings",
        "v4a-07-prices",
        "v4a-08-policy",
        "v4a-09-derived",
    )
    for path, expected in zip(STAGE_WORKFLOWS, expected_names):
        assert _text(path).startswith(f"name: {expected}\n")

def test_release_publisher_recovers_partial_assets_without_overwrite():
    text = _text(Path("scripts/publish_v4a_release_bundle.sh"))
    assert 'missing_assets=()' in text
    assert 'missing_assets+=("${DIR}/${name}")' in text
    assert 'gh release upload "$TAG" "${missing_assets[@]}"' in text
    assert 'persistent bundle asset exists with different bytes' in text
    assert 'persistent bundle asset verification mismatch' in text
    assert "--clobber" not in text
    assert 'gh release view "$TAG" >/dev/null 2>&1 || {' in text



def test_szse_migration_workflow_is_manual_and_producer_isolated():
    from tech_sentiment.v4a_persistent_stage import STAGE_SPECS

    text = _text(SZSE_MIGRATION)
    assert text.startswith("name: v4a-04b-issuer-szse-migration\n")
    assert "materialize_v4a_szse_issuer.py" in text
    assert "--family issuer_szse" in text
    assert "workflow_dispatch:" in text
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text

    assert STAGE_SPECS["issuer_cninfo"].extra_files == (
        ".github/workflows/v4a-issuer-source.yml",
    )
    assert STAGE_SPECS["issuer_sse"].extra_files == (
        ".github/workflows/v4a-issuer-source.yml",
    )
    szse = STAGE_SPECS["issuer_szse"]
    assert "scripts/materialize_v4a_szse_issuer.py" in szse.entrypoints
    assert "scripts/materialize_pit_evidence.py" in szse.entrypoints
    assert ".github/workflows/v4a-issuer-szse-migration.yml" in szse.extra_files
    assert (
        "reference/v4a_szse_security_code_migration_contract_v1.json"
        in szse.extra_files
    )
