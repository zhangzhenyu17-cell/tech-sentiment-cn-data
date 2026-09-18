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
    assert "--symbols-csv output/pit_symbol_scope/capital_pit_symbols.csv" in text


def test_v4a_checkpoint_cache_namespace_matches_v2_exact_identity_architecture():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "capital-pit-v4a3-" not in text
    assert "capital-pit-v4a4-${{ github.sha }}-${{ inputs.start_date }}-" in text
    assert (
        "capital-pit-v4a4-${{ github.sha }}-${{ inputs.start_date }}-\n"
        not in text
    )
    assert "actions/cache/restore@v4" in text
    assert text.count("actions/cache/save@v4") >= 4
    assert "--source-commit \"${{ github.sha }}\"" in text


def test_cninfo_protocol_probe_runs_before_expensive_materialization():
    text = WORKFLOW.read_text(encoding="utf-8")
    probe = text.index("CNINFO protocol connectivity preflight")
    capital = text.index("Materialize ETF-share and SSE+SZSE turnover inputs")
    assert probe < capital
    assert "python scripts/check_cninfo_connectivity.py" in text
    assert "timeout-minutes: 360" in text
