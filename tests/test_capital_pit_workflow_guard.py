from pathlib import Path


def test_capital_pit_materialization_workflow_remains_manual_only():
    path = Path(".github/workflows/qualify-capital-inputs.yml")
    text = path.read_text(encoding="utf-8")
    trigger = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "workflow_run:" not in trigger
    assert "pull_request:" not in trigger
    assert "push:" not in trigger
    assert "scripts/materialize_capital_pit_inputs.py" in text
    assert "capital-pit-materialization" in text
