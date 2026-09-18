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
        marker = f"  {job}:\n"
        start = text.index(marker) + len(marker)
        next_job = re.search(r"(?m)^  [A-Za-z0-9_]+:\s*$", text[start:])
        end = start + next_job.start() if next_job is not None else len(text)
        block = text[start:end]
        assert "needs:" in block
        assert "preflight_gate" in block
        assert "if: ${{ inputs.preflight_only != true }}" in block
    assert "needs.preflight.outputs.end_date" not in text


def test_finalizer_remains_single_canonical_bundle_authority():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("finalize_capital_pit_materialization.py") == 1
    assert text.count("name: capital-pit-input-materialization\n") == 1
    assert text.count("write_capital_pit_artifact_receipt.py") == 1


def test_qualification_blocker_guards_run_after_diagnostics_are_preserved():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("assert_v4a_stage_qualifiable.py") == 8
    expected = {
        "capital": "--kind capital",
        "financing": "--kind financing",
        "issuer_cninfo": "--kind issuer",
        "issuer_sse": "--kind issuer",
        "issuer_szse": "--kind issuer",
        "fundamental_earnings": "--kind fundamental_earnings",
        "policy": "--kind policy",
        "derived_aggregate": "--kind derived",
    }
    for job, kind in expected.items():
        marker = f"  {job}:\n"
        start = text.index(marker) + len(marker)
        next_job = re.search(r"(?m)^  [A-Za-z0-9_]+:\s*$", text[start:])
        end = start + next_job.start() if next_job is not None else len(text)
        block = text[start:end]
        assert kind in block
        assert block.index("actions/upload-artifact@v4") < block.index(
            "assert_v4a_stage_qualifiable.py"
        )

    prices_start = text.index("  prices:\n")
    prices_next = re.search(
        r"(?m)^  [A-Za-z0-9_]+:\s*$",
        text[prices_start + len("  prices:\n"):],
    )
    prices_end = (
        prices_start + len("  prices:\n") + prices_next.start()
        if prices_next is not None
        else len(text)
    )
    assert "assert_v4a_stage_qualifiable.py" not in text[prices_start:prices_end]


def test_preflight_observability_persists_diagnostics_without_changing_gate():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "  preflight_report:\n" in text
    report_start = text.index("  preflight_report:\n")
    gate_start = text.index("  preflight_gate:\n")
    report = text[report_start:gate_start]
    assert "if: ${{ always() }}" in report
    assert (
        "needs: [preflight_static, preflight_cninfo, preflight_policy, "
        "preflight_issuer, preflight_shared]"
    ) in report
    assert "GITHUB_STEP_SUMMARY" in report
    assert "v4a-preflight-*-diagnostics" in report

    for name in ("static", "cninfo", "policy", "issuer", "shared"):
        assert f"v4a-preflight-{name}-diagnostics" in text

    assert text.count("if: ${{ always() }}") >= 6
    assert text.count("set -o pipefail") >= 8
    assert text.count("path: diagnostics") >= 5

    gate_end_match = re.search(
        r"(?m)^  [A-Za-z0-9_]+:\s*$",
        text[gate_start + len("  preflight_gate:\n"):],
    )
    gate_end = (
        gate_start + len("  preflight_gate:\n") + gate_end_match.start()
        if gate_end_match is not None
        else len(text)
    )
    gate = text[gate_start:gate_end]
    assert "preflight_report" not in gate
    assert (
        "needs: [preflight_static, preflight_cninfo, preflight_policy, "
        "preflight_issuer, preflight_shared]"
    ) in gate
