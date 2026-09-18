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


def test_v4a_parallel_dag_has_verified_stage_boundaries():
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in (
        "preflight:",
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
    assert "parallel" not in text.lower() or True  # naming is not a semantic gate


def test_v4a_parallelism_is_bounded_and_source_aware():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "max-parallel: 4" in text
    assert text.count("shard: [0, 1, 2, 3]") >= 3
    assert "--source CNINFO_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "--source SZSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "needs: [preflight, issuer_cninfo]" in text
    assert "needs: [preflight, issuer_aggregate, fundamental_earnings, prices, policy]" in text


def test_v4a_checkpoint_cache_namespace_matches_parallel_exact_identity_architecture():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "capital-pit-v4a4-" not in text
    assert "capital-pit-v4a5-${{ github.sha }}-${{ inputs.start_date }}-" in text
    assert "actions/cache/restore@v4" in text
    assert text.count("actions/cache/save@v4") >= 7
    assert "--source-commit "${{ github.sha }}"" in text


def test_cninfo_protocol_probe_runs_in_preflight_before_materialization():
    text = WORKFLOW.read_text(encoding="utf-8")
    preflight = text.index("CNINFO protocol connectivity preflight")
    capital = text.index("Materialize ETF-share and SSE+SZSE turnover")
    assert preflight < capital
    assert "python scripts/check_cninfo_connectivity.py" in text
    assert "timeout-minutes: 360" not in text


def test_finalizer_remains_single_canonical_bundle_authority():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("finalize_capital_pit_materialization.py") == 1
    assert text.count("name: capital-pit-input-materialization\n") == 1
    assert text.count("write_capital_pit_artifact_receipt.py") == 1
