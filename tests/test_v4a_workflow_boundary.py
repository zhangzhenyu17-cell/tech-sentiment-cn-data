from pathlib import Path
import re


WORKFLOW = Path(".github/workflows/qualify-capital-inputs.yml")


def test_v4a_materialization_workflow_is_manual_only_and_has_no_universe_override():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "workflow_run:" not in text
    assert "pull_request:" not in text
    assert "push:" not in text
    assert "${{ inputs.pit_symbols }}" not in text
    assert re.search(r"(?m)^\s+pit_symbols:\s*$", text) is None
    assert "--symbols-csv stage/shared/pit_symbol_scope/capital_pit_symbols.csv" in text
    assert "preflight_only:" in text
    assert 'description: "Run fast-fail preflights only; skip expensive materialization"' in text
    assert re.search(r"(?ms)^      preflight_only:\n.*?default: false\n.*?type: boolean", text)


def test_v4a_parallel_dag_has_verified_stage_boundaries():
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in (
        "preflight_static:",
        "preflight_cninfo:",
        "preflight_policy:",
        "preflight_issuer:",
        "preflight_shared:",
        "preflight_gate:",
        "capital:",
        "financing:",
        "issuer_cninfo:",
        "issuer_sse:",
        "issuer_szse:",
        "issuer_aggregate:",
        "fundamental_earnings:",
        "prices:",
        "policy:",
        "derived_aggregate:",
        "finalize:",
    ):
        assert job in text
    assert text.count("write_v4a_stage_receipt.py") >= 8
    assert text.count("verify_v4a_stage_receipt.py") >= 5
    assert "merge-multiple: true" in text
    assert "stage/issuer_aggregate" in text
    assert "stage/derived/pit_evidence_materialization" in text


def test_v4a_parallelism_is_bounded_and_source_aware():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "max-parallel: 4" in text
    assert text.count("shard: [0, 1, 2, 3]") >= 3
    assert "--source CNINFO_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SZSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "needs: [preflight_gate, issuer_cninfo]" in text
    assert "needs: [preflight_gate, issuer_aggregate, fundamental_earnings, prices, policy]" in text
    assert "needs: [preflight_static, preflight_cninfo, preflight_policy, preflight_issuer, preflight_shared]" in text


def test_v4a_checkpoint_cache_namespace_matches_parallel_exact_identity_architecture():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "capital-pit-v4a4-" not in text
    assert "capital-pit-v4a5-${{ github.sha }}-${{ inputs.start_date }}-" in text
    assert "actions/cache/restore@v4" in text
    assert text.count("actions/cache/save@v4") >= 7
    assert '--source-commit "${{ github.sha }}"' in text


def test_fast_fail_preflights_run_before_expensive_materialization():
    text = WORKFLOW.read_text(encoding="utf-8")
    capital = text.index("Materialize ETF-share and SSE+SZSE turnover")
    for marker in (
        "CNINFO protocol, PDF, facts and earnings smoke",
        "CSRC policy protocol smoke",
        "SSE and SZSE issuer archive protocol smoke",
        "Latest public-source freshness preflight",
        "Audit public tree and run tests",
    ):
        assert text.index(marker) < capital

    assert "timeout 9m python scripts/check_cninfo_connectivity.py" in text
    assert "timeout 7m python scripts/check_csrc_policy_connectivity.py" in text
    assert "timeout 7m python scripts/check_issuer_archive_connectivity.py" in text
    assert "timeout 10m python scripts/check_v4a_source_freshness.py" in text
    assert "timeout 8m bash -c 'python scripts/audit_public_tree.py && pytest -q'" in text
    assert "timeout-minutes: 360" not in text


def test_all_expensive_jobs_are_blocked_by_unified_fast_fail_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in (
        "capital",
        "financing",
        "issuer_cninfo",
        "issuer_sse",
        "issuer_szse",
        "issuer_aggregate",
        "fundamental_earnings",
        "prices",
        "policy",
        "derived_aggregate",
        "finalize",
    ):
        match = re.search(
            rf"(?ms)^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|\\Z)",
            text,
        )
        assert match is not None
        block = match.group(1)
        assert "needs:" in block
        assert "preflight_gate" in block
        assert "if: ${{ inputs.preflight_only != true }}" in block
    assert "needs.preflight.outputs.end_date" not in text


def test_finalizer_remains_single_canonical_bundle_authority():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("finalize_capital_pit_materialization.py") == 1
    assert text.count("name: capital-pit-input-materialization\n") == 1
    assert text.count("write_capital_pit_artifact_receipt.py") == 1
